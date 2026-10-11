# ReleaseWatch

[![CI](https://github.com/DevFrancisA/releasewatch/actions/workflows/ci.yml/badge.svg)](https://github.com/DevFrancisA/releasewatch/actions/workflows/ci.yml)

ReleaseWatch is a deployment dashboard. It receives webhooks from CI, health-checks each release after it deploys, and tracks rollbacks through to completion.

When CI reports a finished deployment, a background worker probes the release's health endpoint several times and marks it **healthy** or **unhealthy**. An unhealthy release can be rolled back from the UI or automatically, and ReleaseWatch tracks the rollback until CI confirms it. Every step is recorded on the release's timeline.

![Dashboard](docs/screenshots/dashboard.png)

| Release list | Release detail |
|---|---|
| ![Releases](docs/screenshots/releases.png) | ![Release detail](docs/screenshots/release-detail.png) |

## Features

- **CI webhook ingestion.** Webhooks are signed with HMAC-SHA256 and deduplicated by delivery id. The events of one deployment are linked into a single release, and fields that arrive late, like a health URL, are merged in. A late or out-of-order event is recorded but never corrupts state.
- **Post-deployment verification.** The worker runs N probes spaced over time. Network errors and non-2xx responses count as failures, and the release is marked unhealthy once failures reach a threshold.
- **Rollback tracking.** You can request a rollback manually or let it happen automatically when checks fail. ReleaseWatch picks the newest earlier healthy release as the target. It can notify CI through a rollback webhook (retried with exponential backoff) and records CI's `rollback.completed` or `rollback.failed` reply.
- **Dashboard.** Shows change failure rate, status breakdown and per-service health. The release list filters by service, environment and status and pages with a cursor. The release detail page updates itself while checks or a rollback are in progress.
- **Fast reads on large histories.** Composite indexes, keyset pagination and batched queries cut overall p95 latency from **214.9 ms to 22.8 ms** on a 50,000-event dataset. See [docs/benchmarks.md](docs/benchmarks.md).

## Architecture

```mermaid
flowchart LR
    CI[CI pipeline] -- signed webhooks --> API
    UI[React UI] -- /api --> API
    subgraph app [Docker image]
      API[FastAPI<br/>API + static UI]
      W[Worker<br/>python -m releasewatch.worker]
    end
    API -- releases, events,<br/>enqueue jobs --> DB[(PostgreSQL)]
    W -- claim jobs<br/>FOR UPDATE SKIP LOCKED --> DB
    W -- GET health_url --> SVC[Deployed service]
    W -- POST rollback request --> CI
```

- **API** (`backend/releasewatch`): FastAPI with SQLAlchemy 2.0 and psycopg 3. All status changes go through a single state machine in [`lifecycle.py`](backend/releasewatch/lifecycle.py), so webhooks, the UI and the worker follow the same rules and write the same audit events.
- **Worker** ([`worker.py`](backend/releasewatch/worker.py)): uses Postgres as the job queue, with no separate broker. Jobs are claimed with `SELECT … FOR UPDATE SKIP LOCKED`, so you can run as many workers as you like. HTTP probes run outside database transactions. Jobs stuck by a dead worker are requeued automatically.
- **Frontend** (`frontend/`): Vite, React 19 and TypeScript, built into the same Docker image and served by FastAPI.
- **Migrations**: Alembic. CI runs `alembic check` so the models and migrations can't drift apart.

## Quick start

You need Docker.

```bash
docker compose up -d --build
open http://localhost:8000          # UI; API docs at http://localhost:8000/docs
```

Send a deployment through the full flow:

```bash
python scripts/send_webhook.py deployment.started   --service checkout --version v1.0.0
python scripts/send_webhook.py deployment.succeeded --service checkout --version v1.0.0 \
  --health-url http://api:8000/healthz       # probed from inside the compose network
```

The release goes from **Verifying** to **Healthy** as the checks pass. To see a failure, deploy with `--health-url http://api:8000/api/does-not-exist`.

To fill the dashboard with history, run `pip install psycopg` and then `python bench/seed.py`. It loads 50,000 events across 40 services.

## API

See **[docs/api.md](docs/api.md)** for every endpoint with curl examples, the webhook signing scheme, a GitHub Actions snippet and the release state machine. Here are the main calls:

```bash
curl "localhost:8000/api/releases?service=checkout&status=unhealthy"
curl  localhost:8000/api/releases/42
curl -X POST localhost:8000/api/releases/42/rollback -H 'Content-Type: application/json' -d '{"reason":"error rate spike"}'
curl  localhost:8000/api/stats/summary?window_days=30
```

## Development

```bash
# Backend (Python 3.11+)
cd backend
python -m venv .venv && . .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
docker compose up -d db                           # or any Postgres
alembic upgrade head
uvicorn releasewatch.main:app --reload            # API on :8000
python -m releasewatch.worker                     # in another terminal

# Frontend (Node 22+), with a dev server that proxies /api to :8000
cd frontend && npm install && npm run dev         # http://localhost:5173
```

### Tests

| Suite | What it covers | Run |
|---|---|---|
| **pytest** (`backend/tests`, 36 tests) | Integration tests against a real migrated Postgres: webhook signing and idempotency, the state machine, the worker against a mock HTTP transport (passing, failing, flaky and unreachable targets), auto-rollback, retry and backoff, cursor stability, and a query-count guard against N+1 regressions | `docker run -d -p 5433:5432 -e POSTGRES_USER=releasewatch -e POSTGRES_PASSWORD=releasewatch -e POSTGRES_DB=releasewatch_test postgres:16-alpine` then `cd backend && pytest` |
| **Playwright** (`e2e/`, 9 tests) | The full docker compose stack in Chromium: release creation through to healthy, failed health checks, manual, failed and automatic rollback, list paging and filters | `RELEASEWATCH_HEALTH_CHECK_INITIAL_DELAY_SECONDS=1 RELEASEWATCH_HEALTH_CHECK_INTERVAL_SECONDS=1 docker compose up -d --build` then `cd e2e && npm ci && npx playwright install chromium && npx playwright test` |
| **Smoke** (`scripts/smoke.py`) | A deployed instance: liveness on the expected commit, DB readiness, UI shell, read endpoints, signature enforcement, and a signed webhook round trip | `python scripts/smoke.py http://localhost:8000 --webhook-secret dev-webhook-secret` |

## CI/CD

[`.github/workflows/ci.yml`](.github/workflows/ci.yml) runs these jobs:

1. **backend**: ruff lint and format checks, pytest against a Postgres service container, and `alembic check`.
2. **frontend**: TypeScript typecheck and production build.
3. **e2e**: builds the Docker image, starts the stack, runs the smoke script against it, then runs Playwright.
4. **deploy**: runs only on `main`, and only after all three jobs above pass. It triggers the Render deploy hook for that exact commit, waits until `/healthz` reports the new commit, then runs the smoke checks against production.

To turn on deploys, create the services from [`render.yaml`](render.yaml), which has auto-deploy off so only CI can deploy. Then add these to the GitHub `production` environment:
- `RENDER_DEPLOY_HOOK_URL` (secret)
- `WORKER_DEPLOY_HOOK_URL` (secret, optional)
- `PRODUCTION_WEBHOOK_SECRET` (secret, optional)
- `PRODUCTION_URL` (variable)

Until those are set, the deploy job skips with a notice.

## Configuration

All settings are environment variables:

| Variable | Default | |
|---|---|---|
| `DATABASE_URL` | `postgresql+psycopg://releasewatch:releasewatch@localhost:5432/releasewatch` | `postgres://` URLs are accepted |
| `RELEASEWATCH_WEBHOOK_SECRET` | `dev-webhook-secret` | **Change this in production** |
| `RELEASEWATCH_HEALTH_CHECK_COUNT` | `3` | Probes per release |
| `RELEASEWATCH_HEALTH_CHECK_FAILURE_THRESHOLD` | `2` | Failures that make a release unhealthy |
| `RELEASEWATCH_HEALTH_CHECK_INITIAL_DELAY_SECONDS` | `10` | Delay between deploy and the first probe |
| `RELEASEWATCH_HEALTH_CHECK_INTERVAL_SECONDS` | `30` | Delay between probes |
| `RELEASEWATCH_HEALTH_CHECK_TIMEOUT_SECONDS` | `5` | Timeout per probe |
| `RELEASEWATCH_JOB_MAX_ATTEMPTS` | `5` | Retries before a job is marked failed |
| `RELEASEWATCH_CORS_ORIGINS` | `["http://localhost:5173"]` | JSON list |
| `RELEASEWATCH_GIT_SHA` / `RENDER_GIT_COMMIT` | | Commit reported by `/healthz` |

## Project layout

```
backend/
  releasewatch/
    api/            FastAPI routers (webhooks, releases, services, events, stats)
    lifecycle.py    release state machine
    ingest.py       webhook signature check and event correlation
    worker.py       job queue consumer: health checks, rollback notifications
    pagination.py   keyset cursors
  migrations/       Alembic (0001 schema, 0002 composite indexes)
  tests/            pytest integration tests
frontend/           React + Vite + TypeScript
e2e/                Playwright tests
bench/              50k-event seeder and latency benchmark
scripts/            send_webhook.py, smoke.py
docs/               API reference, benchmark write-up, screenshots
```
