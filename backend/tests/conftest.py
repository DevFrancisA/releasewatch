"""Integration test fixtures.

Tests run against a real PostgreSQL database (migrated with Alembic) because
the code relies on Postgres features: JSONB, SKIP LOCKED, and the query plans
the indexes are built for. Point RELEASEWATCH_TEST_DATABASE_URL at a scratch
database; it is wiped at the start of the run.
"""

from __future__ import annotations

import json
import os
import uuid
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest

TEST_DATABASE_URL = os.environ.get(
    "RELEASEWATCH_TEST_DATABASE_URL",
    "postgresql+psycopg://releasewatch:releasewatch@localhost:5433/releasewatch_test",
)
WEBHOOK_SECRET = "test-secret"

# Configure the app before anything imports settings.
os.environ["DATABASE_URL"] = TEST_DATABASE_URL
os.environ["RELEASEWATCH_WEBHOOK_SECRET"] = WEBHOOK_SECRET
os.environ["RELEASEWATCH_HEALTH_CHECK_INITIAL_DELAY_SECONDS"] = "0"
os.environ["RELEASEWATCH_HEALTH_CHECK_INTERVAL_SECONDS"] = "0"
os.environ["RELEASEWATCH_HEALTH_CHECK_COUNT"] = "3"
os.environ["RELEASEWATCH_HEALTH_CHECK_FAILURE_THRESHOLD"] = "2"
os.environ.pop("RELEASEWATCH_STATIC_DIR", None)

from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import text  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from releasewatch.db import get_engine, get_sessionmaker  # noqa: E402
from releasewatch.ingest import sign  # noqa: E402
from releasewatch.main import create_app  # noqa: E402
from releasewatch.worker import Worker  # noqa: E402

BACKEND_DIR = Path(__file__).resolve().parents[1]
TABLES = ("jobs", "health_checks", "events", "releases", "services")


@pytest.fixture(scope="session", autouse=True)
def migrated_database() -> Iterator[None]:
    engine = get_engine()
    with engine.begin() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE"))
        conn.execute(text("CREATE SCHEMA public"))
    config = Config(str(BACKEND_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_DIR / "migrations"))
    command.upgrade(config, "head")
    yield
    engine.dispose()


@pytest.fixture(autouse=True)
def clean_tables() -> None:
    with get_engine().begin() as conn:
        conn.execute(text(f"TRUNCATE {', '.join(TABLES)} RESTART IDENTITY CASCADE"))


@pytest.fixture
def session() -> Iterator[Session]:
    with get_sessionmaker()() as s:
        yield s


@pytest.fixture
def client() -> Iterator[TestClient]:
    with TestClient(create_app()) as c:
        yield c


class FakeTargets:
    """Stand-in for the services being health-checked.

    Map a URL to a status code (or an exception) and the worker's HTTP client
    will answer with it. Every request is recorded.
    """

    def __init__(self) -> None:
        self.responses: dict[str, list[int | Exception]] = {}
        self.requests: list[httpx.Request] = []

    def respond(self, url: str, *outcomes: int | Exception) -> None:
        """Queue outcomes for a URL; the last one repeats once the queue runs out."""
        self.responses[url] = list(outcomes)

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        queue = self.responses.get(str(request.url))
        if not queue:
            return httpx.Response(404)
        outcome = queue.pop(0) if len(queue) > 1 else queue[0]
        if isinstance(outcome, Exception):
            raise outcome
        return httpx.Response(outcome, json={"status": outcome})


@pytest.fixture
def targets() -> FakeTargets:
    return FakeTargets()


@pytest.fixture
def worker(targets: FakeTargets) -> Worker:
    return Worker(http=httpx.Client(transport=httpx.MockTransport(targets.handler)))


Webhook = Callable[..., httpx.Response]


@pytest.fixture
def send_webhook(client: TestClient) -> Webhook:
    def send(
        payload: dict[str, Any],
        *,
        delivery_id: str | None = None,
        secret: str = WEBHOOK_SECRET,
        signature: str | None = None,
    ) -> httpx.Response:
        body = json.dumps(payload).encode()
        headers = {
            "Content-Type": "application/json",
            "X-ReleaseWatch-Signature": signature or sign(body, secret),
            "X-ReleaseWatch-Delivery": delivery_id or str(uuid.uuid4()),
        }
        return client.post("/api/webhooks/ci", content=body, headers=headers)

    return send


def deploy_payload(event: str = "deployment.succeeded", **overrides: Any) -> dict[str, Any]:
    payload = {
        "event": event,
        "service": "checkout",
        "environment": "production",
        "version": "v1.0.0",
        "deployment_id": "run-1",
        "commit_sha": "abc1234",
        "health_url": "http://checkout.internal/healthz",
        "actor": "ci-bot",
    }
    payload.update(overrides)
    return {k: v for k, v in payload.items() if v is not None}
