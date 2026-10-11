#!/usr/bin/env python3
"""Seed a ReleaseWatch database with a realistic event history.

Generates releases for N services across three environments over the last 90
days until exactly --events events exist, with matching health checks and
rollback targets. Rows are loaded with COPY, so 50k events take a few seconds.

    python bench/seed.py --database-url postgresql://releasewatch:releasewatch@localhost:5432/releasewatch
"""

from __future__ import annotations

import argparse
import json
import os
import random
from datetime import UTC, datetime, timedelta

import psycopg

ENVIRONMENTS = [("production", 0.5), ("staging", 0.35), ("canary", 0.15)]
STATUS_WEIGHTS = [
    ("healthy", 0.80),
    ("failed", 0.06),
    ("unhealthy", 0.06),
    ("rolled_back", 0.04),
    ("rolling_back", 0.02),
    ("verifying", 0.01),
    ("deploying", 0.01),
]
EVENTS_BY_STATUS = {
    "deploying": ["deployment.started"],
    "failed": ["deployment.started", "deployment.failed"],
    "verifying": ["deployment.started", "deployment.succeeded", "verification.started"],
    "healthy": [
        "deployment.started",
        "deployment.succeeded",
        "verification.started",
        "verification.passed",
    ],
    "unhealthy": [
        "deployment.started",
        "deployment.succeeded",
        "verification.started",
        "verification.failed",
    ],
    "rolling_back": [
        "deployment.started",
        "deployment.succeeded",
        "verification.started",
        "verification.failed",
        "rollback.requested",
    ],
    "rolled_back": [
        "deployment.started",
        "deployment.succeeded",
        "verification.started",
        "verification.failed",
        "rollback.requested",
        "rollback.completed",
    ],
}
CHECKS_BY_STATUS = {
    "healthy": ["passed", "passed", "passed"],
    "verifying": ["passed"],
    "unhealthy": ["failed", "failed"],
    "rolling_back": ["failed", "passed", "failed"],
    "rolled_back": ["failed", "failed"],
}
SERVICE_WORDS = [
    "checkout", "search", "billing", "auth", "catalog", "inventory", "payments", "ledger",
    "notifications", "gateway", "profile", "orders", "shipping", "pricing", "reviews",
    "recommendations", "analytics", "media", "export", "scheduler",
]  # fmt: skip


def pick(rng: random.Random, weighted: list[tuple[str, float]]) -> str:
    return rng.choices([v for v, _ in weighted], weights=[w for _, w in weighted])[0]


def libpq_url(url: str) -> str:
    return url.replace("postgresql+psycopg://", "postgresql://", 1)


def seed(database_url: str, total_events: int, services: int, seed_value: int) -> None:
    rng = random.Random(seed_value)
    now = datetime.now(UTC)
    start = now - timedelta(days=90)
    words = len(SERVICE_WORDS)
    service_names = [
        SERVICE_WORDS[i % words] + (f"-{i // words}" if i >= words else "") for i in range(services)
    ]

    releases: list[tuple] = []
    events: list[tuple] = []
    checks: list[tuple] = []
    last_healthy: dict[tuple[int, str], int] = {}
    versions: dict[int, int] = {}

    # Spread release start times uniformly over the window, oldest first.
    release_id = 0
    times: list[datetime] = []
    while len(events) < total_events:
        release_id += 1
        service_id = rng.randint(1, services)
        environment = pick(rng, ENVIRONMENTS)
        status = pick(rng, STATUS_WEIGHTS)
        versions[service_id] = versions.get(service_id, 0) + 1
        version = f"v{versions[service_id] // 100}.{versions[service_id] % 100}.0"
        times.append(start + timedelta(seconds=rng.uniform(0, 90 * 86400)))
        releases.append((release_id, service_id, environment, version, status))
        for offset, event_type in enumerate(EVENTS_BY_STATUS[status]):
            if len(events) >= total_events:
                break
            events.append((release_id, service_id, event_type, offset))

    times.sort()
    release_rows = []
    for (rid, sid, env, version, status), created_at in zip(releases, times, strict=True):
        key = (sid, env)
        target = last_healthy.get(key) if status in ("rolling_back", "rolled_back") else None
        if status == "healthy":
            last_healthy[key] = rid
        deployed = created_at + timedelta(minutes=4) if status != "deploying" else None
        unverified = status in ("deploying", "verifying")
        verified = None if unverified else created_at + timedelta(minutes=6)
        rolling = status in ("rolling_back", "rolled_back")
        sha = f"{rng.getrandbits(40):010x}"
        release_rows.append(
            (
                rid, sid, env, version, f"run-{rid}", sha, status,
                f"https://{service_names[sid - 1]}.{env}.internal/healthz",
                f"https://ci.example.com/runs/{rid}", rng.choice(["ci-bot", "anto", "train"]),
                3 if status != "failed" else 0, created_at, verified or deployed or created_at,
                deployed, verified, target,
                "health checks failed" if target else None,
                created_at + timedelta(minutes=7) if rolling else None,
                created_at + timedelta(minutes=12) if status == "rolled_back" else None,
            )
        )  # fmt: skip
        for seq, outcome in enumerate(CHECKS_BY_STATUS.get(status, []), start=1):
            checks.append(
                (
                    rid, seq, outcome, 200 if outcome == "passed" else 503,
                    rng.randint(20, 400), None if outcome == "passed" else "HTTP 503",
                    created_at + timedelta(minutes=4, seconds=30 * seq),
                )
            )  # fmt: skip

    created = {row[0]: row[11] for row in release_rows}
    event_rows = [
        (
            sid, rid, event_type, "worker" if event_type.startswith("verification") else "ci",
            json.dumps({"seq": offset}), created[rid] + timedelta(minutes=2 * offset),
        )
        for rid, sid, event_type, offset in events
    ]  # fmt: skip

    with psycopg.connect(libpq_url(database_url)) as conn, conn.cursor() as cur:
        cur.execute(
            "TRUNCATE jobs, health_checks, events, releases, services RESTART IDENTITY CASCADE"
        )
        with cur.copy("COPY services (id, name, repository) FROM STDIN") as copy:
            for i, name in enumerate(service_names, start=1):
                copy.write_row((i, name, f"acme/{name}"))
        with cur.copy(
            "COPY releases (id, service_id, environment, version, external_id, commit_sha, status,"
            " health_url, ci_run_url, triggered_by, checks_required, created_at, updated_at,"
            " deployed_at, verified_at, rollback_target_id, rollback_reason,"
            " rollback_requested_at, rollback_completed_at) FROM STDIN"
        ) as copy:
            for row in release_rows:
                copy.write_row(row)
        with cur.copy(
            "COPY health_checks (release_id, sequence, outcome, http_status, latency_ms, error,"
            " checked_at) FROM STDIN"
        ) as copy:
            for row in checks:
                copy.write_row(row)
        with cur.copy(
            "COPY events (service_id, release_id, event_type, source, payload, received_at)"
            " FROM STDIN"
        ) as copy:
            for row in event_rows:
                copy.write_row(row)
        for table in ("services", "releases", "health_checks", "events"):
            cur.execute(
                f"SELECT setval(pg_get_serial_sequence('{table}', 'id'),"
                f" (SELECT max(id) FROM {table}))"
            )
        cur.execute("ANALYZE")

    print(
        f"seeded {services} services, {len(release_rows)} releases, "
        f"{len(checks)} health checks, {len(event_rows)} events"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--database-url",
        default=os.environ.get(
            "DATABASE_URL", "postgresql://releasewatch:releasewatch@localhost:5432/releasewatch"
        ),
    )
    parser.add_argument("--events", type=int, default=50_000)
    parser.add_argument("--services", type=int, default=40)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()
    seed(args.database_url, args.events, args.services, args.seed)


if __name__ == "__main__":
    main()
