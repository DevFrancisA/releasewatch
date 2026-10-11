from sqlalchemy import select

from releasewatch.models import Event, Job, Release
from tests.conftest import deploy_payload


def test_rejects_missing_or_bad_signature(client, send_webhook):
    body = deploy_payload()
    assert client.post("/api/webhooks/ci", json=body).status_code == 401
    assert send_webhook(body, secret="wrong").status_code == 401
    assert send_webhook(body, signature="sha256=deadbeef").status_code == 401


def test_rejects_invalid_payload(send_webhook):
    response = send_webhook({"event": "deployment.exploded", "service": "Checkout!"})
    assert response.status_code == 422
    fields = {err["loc"][-1] for err in response.json()["detail"]}
    assert {"event", "service", "version"} <= fields


def test_deployment_lifecycle_creates_release_and_schedules_checks(send_webhook, session):
    started = send_webhook(deploy_payload("deployment.started"))
    assert started.status_code == 202
    assert started.json()["release_status"] == "deploying"

    succeeded = send_webhook(deploy_payload("deployment.succeeded"))
    body = succeeded.json()
    assert body["applied"] is True
    assert body["release_id"] == started.json()["release_id"]
    assert body["release_status"] == "verifying"

    release = session.get(Release, body["release_id"])
    assert release.deployed_at is not None
    assert release.checks_required == 3
    jobs = session.scalars(select(Job)).all()
    assert [(j.kind, j.payload) for j in jobs] == [
        ("health_check", {"release_id": release.id, "sequence": 1})
    ]

    timeline = session.scalars(
        select(Event.event_type).where(Event.release_id == release.id).order_by(Event.id)
    ).all()
    assert timeline == ["deployment.started", "deployment.succeeded", "verification.started"]


def test_later_events_fill_in_release_metadata(send_webhook, session):
    started = send_webhook(deploy_payload("deployment.started", health_url=None, commit_sha=None))
    succeeded = send_webhook(
        deploy_payload(ci_run_url="https://ci.example.com/runs/1", commit_sha="feed123")
    )
    assert succeeded.json()["release_status"] == "verifying"

    release = session.get(Release, started.json()["release_id"])
    assert release.health_url == "http://checkout.internal/healthz"
    assert release.commit_sha == "feed123"
    assert release.ci_run_url == "https://ci.example.com/runs/1"


def test_redelivered_webhook_is_idempotent(send_webhook, session):
    first = send_webhook(deploy_payload(), delivery_id="delivery-1")
    second = send_webhook(deploy_payload(), delivery_id="delivery-1")

    assert second.status_code == 202
    assert second.json()["duplicate"] is True
    assert second.json()["event_id"] == first.json()["event_id"]
    assert len(session.scalars(select(Job)).all()) == 1


def test_deployment_failed(send_webhook):
    send_webhook(deploy_payload("deployment.started"))
    response = send_webhook(deploy_payload("deployment.failed"))
    assert response.json()["release_status"] == "failed"


def test_success_without_health_url_skips_verification(send_webhook, session):
    response = send_webhook(deploy_payload(health_url=None))
    assert response.json()["release_status"] == "healthy"
    assert session.scalars(select(Job)).all() == []


def test_service_default_health_url_is_used(client, send_webhook):
    client.put(
        "/api/services/checkout",
        json={"name": "checkout", "default_health_url": "http://checkout.internal/ready"},
    )
    response = send_webhook(deploy_payload(health_url=None))
    assert response.json()["release_status"] == "verifying"
    detail = client.get(f"/api/releases/{response.json()['release_id']}").json()
    assert detail["health_url"] == "http://checkout.internal/ready"


def test_late_event_for_finished_deployment_is_recorded_but_not_applied(send_webhook):
    send_webhook(deploy_payload("deployment.failed"))
    response = send_webhook(deploy_payload("deployment.succeeded"))
    assert response.json()["applied"] is False
    assert response.json()["release_status"] == "failed"
    assert "already failed" in response.json()["detail"]


def test_events_without_deployment_id_correlate_by_version(send_webhook):
    a = send_webhook(deploy_payload("deployment.started", deployment_id=None, version="v2"))
    b = send_webhook(deploy_payload("deployment.succeeded", deployment_id=None, version="v2"))
    c = send_webhook(deploy_payload("deployment.started", deployment_id=None, version="v3"))
    assert a.json()["release_id"] == b.json()["release_id"] != c.json()["release_id"]


def test_rollback_event_for_unknown_deployment(send_webhook):
    response = send_webhook(deploy_payload("rollback.completed", deployment_id="nope"))
    assert response.status_code == 202
    assert response.json()["applied"] is False
    assert response.json()["release_id"] is None
