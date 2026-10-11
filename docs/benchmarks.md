# API latency benchmark

## Setup

- **Dataset:** `bench/seed.py` loads a deterministic history: 40 services, 12,698 releases, 33,893 health checks, and **50,000 events** spread over 90 days.
- **Workload:** `bench/run.py` replays a dashboard session from 4 concurrent clients, 8 sessions each (1,248 requests). Each session loads:
  - the 30-day summary,
  - the newest releases, unfiltered and filtered by service and by status,
  - 20 pages of release history (50 per page),
  - 5 release detail pages,
  - 10 pages of one service's event log.
- **Stack:** `docker compose` (one uvicorn process, Postgres 16) on Docker Desktop for Windows. Absolute numbers depend on the host. Compare the two runs with each other, not with other machines.

## Results

| Endpoint | Baseline p95 | Optimized p95 |
|---|---:|---:|
| `GET /api/releases` (first page) | 135.4 ms | 20.2 ms |
| `GET /api/releases` (paging through history) | 214.8 ms | 23.8 ms |
| `GET /api/releases?service=` | 100.5 ms | 22.7 ms |
| `GET /api/releases?status=` | 130.4 ms | 22.5 ms |
| `GET /api/stats/summary` | 268.3 ms | 37.6 ms |
| `GET /api/releases/{id}` | 19.5 ms | 19.0 ms |
| `GET /api/events?service=` (paging) | 16.6 ms | 16.5 ms |
| **All requests** | **214.9 ms** | **22.8 ms** |

The whole workload took 37.4 s before and 5.4 s after. About 13 ms of every request is fixed cost from HTTP and the Docker network on this host; the fastest endpoints show that floor.

## What changed (commit "Speed up list endpoints…")

1. **Batched queries.** Before, each release row in a list issued its own queries: the service lazy-load, the health-check list, and an event `COUNT(*)`. That came to about 2 + 3N queries per page. The list now joins the service in and computes health and event counts with two `GROUP BY release_id` aggregates, so a page always takes 3 queries whatever its size. `test_release_list_query_count_does_not_grow_with_page_size` guards this.
   The summary went from 2 queries per service to 3 in total: one grouped count, one `DISTINCT ON (service_id)` for the latest release, and the service names.
2. **Cursor pagination.** `OFFSET` makes Postgres read and discard every skipped row, and it needs a separate `COUNT(*)` for the total. Lists now page by `WHERE (created_at, id) < (:ts, :id)` with an opaque cursor. That is one index range scan at any depth, and pages don't shift when new deployments arrive mid-browse.
3. **Composite indexes** (`migrations/versions/0002_composite_indexes.py`):
   - `releases (created_at, id)`, `(service_id, created_at, id)`, `(service_id, environment, created_at, id)`, `(status, created_at, id)`. Each filter-plus-sort combination is served in index order; Postgres scans backward for `DESC`.
   - `events (release_id, received_at, id)` for timelines and per-release counts, `(service_id, received_at, id)` for service event logs, and `(received_at, id)`.
   - A partial index on `jobs (run_at, id) WHERE status = 'queued'` for the worker's claim query.

## Reproduce

```bash
docker compose up -d --build
python bench/seed.py          # needs psycopg; uses DATABASE_URL or localhost:5432
python bench/run.py --label optimized
```

To measure the baseline, check out the commit before the optimization (`git log --oneline -- backend/releasewatch/pagination.py`), rebuild, reseed, and run with `--label baseline`. `bench/run.py` follows `next_cursor` when the API returns one and falls back to `?page=N` otherwise, so the same workload runs against both versions.
