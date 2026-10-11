#!/usr/bin/env python3
"""Measure ReleaseWatch API latency under a dashboard-style read workload.

The workload mirrors how the UI is used: open the dashboard (summary +
newest releases), filter by service / status, page through history, open a
release, and browse a service's event log. Requests are issued from several
concurrent clients and per-endpoint p50/p95/p99 latencies are reported.

Works against both pagination styles: follows `next_cursor` when the API
returns one, and falls back to `page=N` otherwise.

    python bench/run.py --label optimized --url http://localhost:8000
"""

from __future__ import annotations

import argparse
import json
import random
import statistics
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx

RESULTS_DIR = Path(__file__).parent / "results"


def percentile(values: list[float], pct: float) -> float:
    ordered = sorted(values)
    k = (len(ordered) - 1) * pct / 100
    lo, hi = int(k), min(int(k) + 1, len(ordered) - 1)
    return ordered[lo] + (ordered[hi] - ordered[lo]) * (k - lo)


class Session:
    def __init__(self, base_url: str, rng: random.Random) -> None:
        self.http = httpx.Client(base_url=base_url, timeout=60)
        self.rng = rng
        self.samples: list[tuple[str, float]] = []

    def get(self, name: str, path: str, params: dict | None = None) -> dict:
        started = time.perf_counter()
        response = self.http.get(path, params=params)
        elapsed = (time.perf_counter() - started) * 1000
        response.raise_for_status()
        self.samples.append((name, elapsed))
        return response.json()

    def walk(self, name: str, path: str, params: dict, pages: int) -> list[dict]:
        """Page through a listing the way a user clicking 'Load more' would."""
        items: list[dict] = []
        cursor: str | None = None
        for page in range(1, pages + 1):
            query = dict(params)
            if cursor:
                query["cursor"] = cursor
            elif page > 1:
                query["page"] = page
            body = self.get(name, path, query)
            items.extend(body["items"])
            cursor = body.get("next_cursor")
            if not body["items"] or ("next_cursor" in body and not cursor):
                break
        return items

    def user_journey(self, services: list[str]) -> None:
        service = self.rng.choice(services)
        self.get("stats.summary", "/api/stats/summary", {"window_days": 30})
        first = self.get("releases.list", "/api/releases", {"limit": 25, "page_size": 25})
        self.get("releases.list?service", "/api/releases", {"service": service, "limit": 25})
        self.get("releases.list?status", "/api/releases", {"status": "unhealthy", "limit": 25})
        history = self.walk(
            "releases.list (paging)", "/api/releases", {"limit": 50, "page_size": 50}, pages=20
        )
        for release in self.rng.sample(first["items"] + history, k=5):
            self.get("releases.detail", f"/api/releases/{release['id']}")
        self.walk(
            "events.list?service (paging)",
            "/api/events",
            {"service": service, "limit": 50, "page_size": 50},
            pages=10,
        )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--url", default="http://localhost:8000")
    parser.add_argument("--label", default="run")
    parser.add_argument("--clients", type=int, default=4)
    parser.add_argument("--journeys", type=int, default=8, help="journeys per client")
    parser.add_argument("--seed", type=int, default=7)
    args = parser.parse_args()

    with httpx.Client(base_url=args.url, timeout=60) as http:
        services = [s["name"] for s in http.get("/api/services").json()]
        # Warm up connection pools and the database cache.
        for _ in range(3):
            http.get("/api/releases").raise_for_status()
            http.get("/api/stats/summary").raise_for_status()

    sessions = [Session(args.url, random.Random(args.seed + i)) for i in range(args.clients)]

    def run_client(session: Session) -> None:
        for _ in range(args.journeys):
            session.user_journey(services)

    started = time.perf_counter()
    with ThreadPoolExecutor(max_workers=args.clients) as pool:
        list(pool.map(run_client, sessions))
    wall = time.perf_counter() - started

    by_name: dict[str, list[float]] = defaultdict(list)
    for session in sessions:
        for name, ms in session.samples:
            by_name[name].append(ms)
    all_samples = [ms for values in by_name.values() for ms in values]

    def row(values: list[float]) -> dict[str, float]:
        return {
            "n": len(values),
            "p50_ms": round(percentile(values, 50), 1),
            "p95_ms": round(percentile(values, 95), 1),
            "p99_ms": round(percentile(values, 99), 1),
            "mean_ms": round(statistics.fmean(values), 1),
        }

    report = {
        "label": args.label,
        "url": args.url,
        "clients": args.clients,
        "requests": len(all_samples),
        "wall_seconds": round(wall, 1),
        "overall": row(all_samples),
        "endpoints": {name: row(values) for name, values in sorted(by_name.items())},
    }

    print(f"\n{args.label}: {len(all_samples)} requests from {args.clients} clients in {wall:.1f}s")
    print(f"{'endpoint':32} {'n':>5} {'p50':>9} {'p95':>9} {'p99':>9}")
    for name, stats in [*report["endpoints"].items(), ("OVERALL", report["overall"])]:
        print(
            f"{name:32} {stats['n']:>5} {stats['p50_ms']:>7.1f}ms "
            f"{stats['p95_ms']:>7.1f}ms {stats['p99_ms']:>7.1f}ms"
        )

    RESULTS_DIR.mkdir(exist_ok=True)
    out = RESULTS_DIR / f"{args.label}.json"
    out.write_text(json.dumps(report, indent=2))
    print(f"\nwrote {out}")


if __name__ == "__main__":
    main()
