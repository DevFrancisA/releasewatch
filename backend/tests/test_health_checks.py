import httpx
from sqlalchemy import select

from releasewatch.models import Event, HealthCheck, Job, JobStatus, Release
from tests.conftest import deploy_payload

HEALTH_URL = "http://checkout.internal/healthz"


def _release(session, release_id: int) -> Release:
    session.expire_all()
    return session.get(Release, release_id)


def _events(session, release_id: int) -> list[str]:
    return list(
        session.scalars(
            select(Event.event_type).where(Event.release_id == release_id).order_by(Event.id)
        )
    )


def test_all_checks_pass_marks_release_healthy(send_webhook, worker, targets, session):
    targets.respond(HEALTH_URL, 200)
    release_id = send_webhook(deploy_payload()).json()["release_id"]

    assert worker.drain() == 3

    release = _release(session, release_id)
    assert release.status == "healthy"
    assert release.verified_at is not None
    assert [c.outcome for c in release.health_checks] == ["passed"] * 3
    assert all(c.http_status == 200 and c.latency_ms is not None for c in release.health_checks)
    assert _events(session, release_id)[-1] == "verification.passed"
    assert len(targets.requests) == 3


def test_failed_checks_mark_release_unhealthy(send_webhook, worker, targets, session):
    targets.respond(HEALTH_URL, 503)
    release_id = send_webhook(deploy_payload()).json()["release_id"]

    worker.drain()

    release = _release(session, release_id)
    assert release.status == "unhealthy"
    # Verdict is reached at the failure threshold; no third check is scheduled.
    assert [(c.outcome, c.http_status, c.error) for c in release.health_checks] == [
        ("failed", 503, "HTTP 503"),
        ("failed", 503, "HTTP 503"),
    ]
    assert _events(session, release_id)[-1] == "verification.failed"


def test_single_blip_below_threshold_still_passes(send_webhook, worker, targets, session):
    targets.respond(HEALTH_URL, 500, 200)
    release_id = send_webhook(deploy_payload()).json()["release_id"]

    worker.drain()

    release = _release(session, release_id)
    assert release.status == "healthy"
    assert [c.outcome for c in release.health_checks] == ["failed", "passed", "passed"]


def test_network_errors_count_as_failures(send_webhook, worker, targets, session):
    targets.respond(HEALTH_URL, httpx.ConnectError("connection refused"))
    release_id = send_webhook(deploy_payload()).json()["release_id"]

    worker.drain()

    release = _release(session, release_id)
    assert release.status == "unhealthy"
    assert release.health_checks[0].http_status is None
    assert "ConnectError" in release.health_checks[0].error


def test_auto_rollback_targets_previous_healthy_release(
    client, send_webhook, worker, targets, session
):
    client.put(
        "/api/services/checkout",
        json={
            "name": "checkout",
            "auto_rollback": True,
            "rollback_webhook_url": "http://ci.internal/rollback",
        },
    )
    targets.respond(HEALTH_URL, 200)
    targets.respond("http://ci.internal/rollback", 202)
    good_id = send_webhook(deploy_payload(version="v1", deployment_id="run-1")).json()["release_id"]
    worker.drain()

    targets.respond(HEALTH_URL, 500)
    bad_id = send_webhook(deploy_payload(version="v2", deployment_id="run-2")).json()["release_id"]
    worker.drain()

    bad = _release(session, bad_id)
    assert bad.status == "rolling_back"
    assert bad.rollback_target_id == good_id
    assert bad.rollback_reason.startswith("Automatic: 2 of 2 health checks failed")
    assert _events(session, bad_id)[-2:] == ["verification.failed", "rollback.requested"]

    notify = [r for r in targets.requests if r.url.path == "/rollback"]
    assert len(notify) == 1
    assert b'"target_version":"v1"' in notify[0].content.replace(b" ", b"")

    # CI reports back that the rollback finished.
    done = send_webhook(deploy_payload("rollback.completed", version="v2", deployment_id="run-2"))
    assert done.json()["release_status"] == "rolled_back"


def test_check_result_is_ignored_after_manual_rollback(client, send_webhook, worker, targets):
    targets.respond(HEALTH_URL, 200)
    release_id = send_webhook(deploy_payload()).json()["release_id"]
    client.post(f"/api/releases/{release_id}/rollback", json={"reason": "bad config"})

    worker.drain()

    detail = client.get(f"/api/releases/{release_id}").json()
    assert detail["status"] == "rolling_back"
    assert detail["health_checks"] == []


def test_failing_job_is_retried_with_backoff(client, send_webhook, worker, targets, session):
    client.put(
        "/api/services/checkout",
        json={"name": "checkout", "rollback_webhook_url": "http://ci.internal/rollback"},
    )
    targets.respond("http://ci.internal/rollback", 500)
    release_id = send_webhook(deploy_payload(health_url=None)).json()["release_id"]
    client.post(f"/api/releases/{release_id}/rollback", json={"reason": "manual"})

    assert worker.drain() == 1

    job = session.scalars(select(Job).where(Job.kind == "rollback_notify")).one()
    assert job.status == JobStatus.QUEUED
    assert job.attempts == 1
    assert "500" in job.last_error
    assert job.run_at > job.created_at


def test_unknown_job_kind_is_failed_after_max_attempts(worker, session):
    session.add(Job(kind="mystery", payload={}))
    session.commit()

    for _ in range(worker.settings.job_max_attempts):
        session.execute(Job.__table__.update().values(run_at=Job.created_at))
        session.commit()
        worker.drain()

    session.expire_all()
    job = session.scalars(select(Job)).one()
    assert job.status == JobStatus.FAILED
    assert "unknown job kind" in job.last_error


def test_health_checks_are_unique_per_sequence(send_webhook, worker, targets, session):
    targets.respond(HEALTH_URL, 200)
    release_id = send_webhook(deploy_payload()).json()["release_id"]
    worker.drain()
    sequences = session.scalars(
        select(HealthCheck.sequence).where(HealthCheck.release_id == release_id)
    ).all()
    assert sorted(sequences) == [1, 2, 3]
