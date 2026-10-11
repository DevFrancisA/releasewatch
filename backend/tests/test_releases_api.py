from contextlib import contextmanager
from datetime import timedelta

from sqlalchemy import event as sa_event
from sqlalchemy import update

from releasewatch.db import get_engine
from releasewatch.lifecycle import utcnow
from releasewatch.models import Release
from tests.conftest import deploy_payload

HEALTH_URL = "http://checkout.internal/healthz"


def test_create_release_starts_verification(client, worker, targets):
    targets.respond(HEALTH_URL, 200)
    response = client.post(
        "/api/releases",
        json={
            "service": "checkout",
            "environment": "staging",
            "version": "v2.4.0",
            "commit_sha": "9f8e7d6",
            "health_url": HEALTH_URL,
            "triggered_by": "anto",
        },
    )
    assert response.status_code == 201
    created = response.json()
    assert created["status"] == "verifying"
    assert created["health"] == {"passed": 0, "failed": 0, "required": 3}

    worker.drain()

    detail = client.get(f"/api/releases/{created['id']}").json()
    assert detail["status"] == "healthy"
    assert detail["health"] == {"passed": 3, "failed": 0, "required": 3}
    assert [c["sequence"] for c in detail["health_checks"]] == [1, 2, 3]
    assert [e["event_type"] for e in detail["events"]] == [
        "release.created",
        "verification.started",
        "verification.passed",
    ]
    assert detail["event_count"] == 3


def test_create_release_validates_input(client):
    response = client.post(
        "/api/releases", json={"service": "Bad Name", "version": "", "health_url": "ftp://x"}
    )
    assert response.status_code == 422
    fields = {err["loc"][-1] for err in response.json()["detail"]}
    assert fields == {"service", "version", "health_url"}


def test_get_missing_release_returns_404(client):
    assert client.get("/api/releases/999").status_code == 404
    assert client.post("/api/releases/999/rollback", json={"reason": "x"}).status_code == 404


def test_rollback_flow(client, send_webhook, worker, targets):
    targets.respond(HEALTH_URL, 200)
    v1 = send_webhook(deploy_payload(version="v1", deployment_id="d1")).json()["release_id"]
    worker.drain()
    v2 = send_webhook(deploy_payload(version="v2", deployment_id="d2")).json()["release_id"]
    worker.drain()

    response = client.post(f"/api/releases/{v2}/rollback", json={"reason": "error rate spike"})
    assert response.status_code == 200
    assert response.json()["status"] == "rolling_back"
    assert response.json()["rollback_target_id"] == v1

    detail = client.get(f"/api/releases/{v2}").json()
    assert detail["rollback_target_version"] == "v1"
    assert detail["rollback_reason"] == "error rate spike"
    assert detail["events"][-1]["payload"]["target_version"] == "v1"

    # A second rollback request while one is in progress is a conflict.
    again = client.post(f"/api/releases/{v2}/rollback", json={"reason": "again"})
    assert again.status_code == 409

    done = send_webhook(deploy_payload("rollback.completed", version="v2", deployment_id="d2"))
    assert done.json()["release_status"] == "rolled_back"
    assert client.get(f"/api/releases/{v2}").json()["rollback_completed_at"] is not None


def test_failed_rollback_returns_release_to_unhealthy(client, send_webhook):
    release_id = send_webhook(deploy_payload(health_url=None)).json()["release_id"]
    client.post(f"/api/releases/{release_id}/rollback", json={"reason": "manual"})

    response = send_webhook(deploy_payload("rollback.failed"))
    assert response.json()["release_status"] == "unhealthy"


def test_cannot_roll_back_a_deployment_in_progress(client, send_webhook):
    release_id = send_webhook(deploy_payload("deployment.started")).json()["release_id"]
    response = client.post(f"/api/releases/{release_id}/rollback", json={"reason": "x"})
    assert response.status_code == 409
    assert "deploying" in response.json()["detail"]


def test_list_releases_filters_and_orders_newest_first(client, send_webhook):
    for i, (service, env, event) in enumerate(
        [
            ("checkout", "production", "deployment.succeeded"),
            ("checkout", "staging", "deployment.succeeded"),
            ("search", "production", "deployment.failed"),
            ("checkout", "production", "deployment.failed"),
        ]
    ):
        send_webhook(
            deploy_payload(
                event, service=service, environment=env, version=f"v{i}", deployment_id=f"d{i}"
            )
        )

    all_items = client.get("/api/releases").json()["items"]
    assert [r["version"] for r in all_items] == ["v3", "v2", "v1", "v0"]

    def versions(**params):
        return [r["version"] for r in client.get("/api/releases", params=params).json()["items"]]

    assert versions(service="checkout") == ["v3", "v1", "v0"]
    assert versions(service="checkout", environment="production") == ["v3", "v0"]
    assert versions(status="failed") == ["v3", "v2"]
    assert versions(service="unknown") == []
    assert client.get("/api/releases", params={"status": "bogus"}).status_code == 422


def test_list_releases_paginates_without_gaps_or_duplicates(client, send_webhook, session):
    for i in range(23):
        send_webhook(deploy_payload(version=f"v{i}", deployment_id=f"d{i}", health_url=None))
    # Give several releases the same timestamp to exercise the id tie-breaker.
    session.execute(update(Release).where(Release.id <= 10).values(created_at=utcnow()))
    session.commit()

    seen = _collect_pages(client, "/api/releases", limit=5)
    assert len(seen) == 23
    assert len(set(seen)) == 23
    # Rows 1-10 were bumped to "now", so they lead (id DESC breaks the tie).
    assert seen == [*range(10, 0, -1), *range(23, 10, -1)]


def test_cursor_pages_stay_stable_while_new_releases_arrive(client, send_webhook):
    for i in range(6):
        send_webhook(deploy_payload(version=f"v{i}", deployment_id=f"d{i}", health_url=None))

    first = client.get("/api/releases", params={"limit": 3}).json()
    # New deployments land between page loads; OFFSET paging would repeat rows here.
    for i in range(6, 9):
        send_webhook(deploy_payload(version=f"v{i}", deployment_id=f"d{i}", health_url=None))
    second = client.get("/api/releases", params={"limit": 3, "cursor": first["next_cursor"]}).json()

    assert [r["version"] for r in first["items"]] == ["v5", "v4", "v3"]
    assert [r["version"] for r in second["items"]] == ["v2", "v1", "v0"]
    assert second["next_cursor"] is None


def test_invalid_cursor_is_rejected(client):
    assert client.get("/api/releases", params={"cursor": "not-a-cursor"}).status_code == 400
    assert client.get("/api/events", params={"cursor": "e30"}).status_code == 400


def test_release_list_query_count_does_not_grow_with_page_size(client, send_webhook):
    for i in range(30):
        send_webhook(deploy_payload(version=f"v{i}", deployment_id=f"d{i}"))

    with count_queries() as small:
        client.get("/api/releases", params={"limit": 2})
    with count_queries() as large:
        client.get("/api/releases", params={"limit": 30})

    assert len(large) == len(small) <= 3


@contextmanager
def count_queries():
    statements: list[str] = []

    def record(conn, cursor, statement, *args):
        statements.append(statement)

    engine = get_engine()
    sa_event.listen(engine, "before_cursor_execute", record)
    try:
        yield statements
    finally:
        sa_event.remove(engine, "before_cursor_execute", record)


def _collect_pages(client, path: str, limit: int) -> list[int]:
    seen: list[int] = []
    cursor = None
    while True:
        params = {"limit": limit} | ({"cursor": cursor} if cursor else {})
        body = client.get(path, params=params).json()
        seen.extend(item["id"] for item in body["items"])
        cursor = body["next_cursor"]
        if cursor is None:
            return seen


def test_list_events_pages_through_service_history(client, send_webhook):
    for i in range(4):
        send_webhook(deploy_payload(version=f"v{i}", deployment_id=f"d{i}"))
    ids = _collect_pages(client, "/api/events?service=checkout", limit=3)
    assert len(ids) == 8  # deployment.succeeded + verification.started per release
    assert ids == sorted(ids, reverse=True)


def test_list_events_filters(client, send_webhook):
    send_webhook(deploy_payload("deployment.started"))
    send_webhook(deploy_payload("deployment.succeeded"))
    send_webhook(deploy_payload("deployment.started", service="search", deployment_id="s1"))

    body = client.get("/api/events", params={"service": "checkout"}).json()
    assert [e["event_type"] for e in body["items"]] == [
        "verification.started",
        "deployment.succeeded",
        "deployment.started",
    ]
    only_started = client.get("/api/events", params={"event_type": "deployment.started"}).json()
    assert len(only_started["items"]) == 2


def test_summary_reports_change_failure_rate(client, send_webhook, session):
    send_webhook(deploy_payload(version="v1", deployment_id="d1", health_url=None))
    send_webhook(deploy_payload(version="v2", deployment_id="d2", health_url=None))
    send_webhook(deploy_payload("deployment.failed", version="v3", deployment_id="d3"))
    send_webhook(deploy_payload(service="search", deployment_id="s1", health_url=None))
    # Outside the 7 day window.
    old = send_webhook(deploy_payload("deployment.failed", version="v0", deployment_id="d0"))
    session.execute(
        update(Release)
        .where(Release.id == old.json()["release_id"])
        .values(created_at=utcnow() - timedelta(days=30))
    )
    session.commit()

    summary = client.get("/api/stats/summary").json()
    assert summary["releases"] == 4
    assert summary["by_status"]["healthy"] == 3
    assert summary["by_status"]["failed"] == 1
    assert summary["change_failure_rate"] == 0.25

    by_service = {s["service"]: s for s in summary["services"]}
    assert by_service["checkout"]["releases"] == 3
    assert by_service["checkout"]["change_failure_rate"] == round(1 / 3, 4)
    assert by_service["checkout"]["last_release_version"] == "v3"
    assert by_service["search"]["change_failure_rate"] == 0.0


def test_services_upsert_and_get(client):
    payload = {"name": "billing", "auto_rollback": True, "repository": "acme/billing"}
    assert client.put("/api/services/billing", json=payload).status_code == 200
    payload["auto_rollback"] = False
    client.put("/api/services/billing", json=payload)

    service = client.get("/api/services/billing").json()
    assert service["auto_rollback"] is False
    assert service["repository"] == "acme/billing"
    assert [s["name"] for s in client.get("/api/services").json()] == ["billing"]
    assert client.put("/api/services/other", json=payload).status_code == 422
    assert client.get("/api/services/nope").status_code == 404


def test_health_endpoints(client):
    assert client.get("/healthz").json()["status"] == "ok"
    assert client.get("/readyz").json() == {"status": "ready"}
