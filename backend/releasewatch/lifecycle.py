"""Release state machine.

Every status change goes through this module so that webhooks, the UI API and
the worker apply the same rules and leave the same audit trail in `events`.

    deploying ──succeeded──▶ verifying ──checks pass──▶ healthy
        │                        │
        └──failed──▶ failed      └──checks fail──▶ unhealthy
                                                       │
    {verifying, healthy, unhealthy, failed} ──rollback──▶ rolling_back ──▶ rolled_back
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from releasewatch.config import get_settings
from releasewatch.models import (
    CheckOutcome,
    Event,
    HealthCheck,
    Job,
    Release,
    ReleaseStatus,
    Service,
)

S = ReleaseStatus

ROLLBACK_FROM = {S.VERIFYING, S.HEALTHY, S.UNHEALTHY, S.FAILED}

JOB_HEALTH_CHECK = "health_check"
JOB_ROLLBACK_NOTIFY = "rollback_notify"


class TransitionError(Exception):
    """Raised when an action is not valid for the release's current status."""


def utcnow() -> datetime:
    return datetime.now(UTC)


def record_event(
    session: Session,
    *,
    service_id: int,
    release_id: int | None,
    event_type: str,
    source: str,
    payload: dict[str, Any] | None = None,
    delivery_id: str | None = None,
) -> Event:
    event = Event(
        service_id=service_id,
        release_id=release_id,
        event_type=event_type,
        source=source,
        payload=payload or {},
        delivery_id=delivery_id,
        received_at=utcnow(),
    )
    session.add(event)
    return event


def enqueue(session: Session, kind: str, payload: dict[str, Any], delay_seconds: float = 0) -> Job:
    job = Job(kind=kind, payload=payload, run_at=utcnow() + timedelta(seconds=delay_seconds))
    session.add(job)
    return job


def get_or_create_service(session: Session, name: str) -> Service:
    service = session.scalar(select(Service).where(Service.name == name))
    if service is None:
        service = Service(name=name)
        session.add(service)
        session.flush()
    return service


# --------------------------------------------------------------------------- deploy


def mark_deployed(session: Session, release: Release, source: str) -> None:
    """Deployment finished in CI; start post-deployment verification."""
    release.deployed_at = utcnow()
    health_url = release.health_url or release.service.default_health_url
    release.health_url = health_url
    settings = get_settings()

    if not health_url or settings.health_check_count <= 0:
        release.status = S.HEALTHY
        release.verified_at = utcnow()
        record_event(
            session,
            service_id=release.service_id,
            release_id=release.id,
            event_type="verification.skipped",
            source=source,
            payload={"reason": "no health_url configured"},
        )
        return

    release.status = S.VERIFYING
    release.checks_required = settings.health_check_count
    enqueue(
        session,
        JOB_HEALTH_CHECK,
        {"release_id": release.id, "sequence": 1},
        delay_seconds=settings.health_check_initial_delay_seconds,
    )
    record_event(
        session,
        service_id=release.service_id,
        release_id=release.id,
        event_type="verification.started",
        source=source,
        payload={"health_url": health_url, "checks_required": settings.health_check_count},
    )


@dataclass
class NewRelease:
    service: str
    environment: str
    version: str
    external_id: str | None = None
    commit_sha: str | None = None
    health_url: str | None = None
    ci_run_url: str | None = None
    triggered_by: str | None = None


def create_release(session: Session, data: NewRelease, *, status: S, source: str) -> Release:
    service = get_or_create_service(session, data.service)
    release = Release(
        service=service,
        environment=data.environment,
        version=data.version,
        external_id=data.external_id or f"{data.environment}:{data.version}:{utcnow().isoformat()}",
        commit_sha=data.commit_sha,
        health_url=data.health_url,
        ci_run_url=data.ci_run_url,
        triggered_by=data.triggered_by,
        status=status,
        created_at=utcnow(),
    )
    session.add(release)
    session.flush()
    return release


def register_manual_release(session: Session, data: NewRelease) -> Release:
    """A deployment reported from the UI/API rather than CI: verify it immediately."""
    release = create_release(session, data, status=S.DEPLOYING, source="api")
    record_event(
        session,
        service_id=release.service_id,
        release_id=release.id,
        event_type="release.created",
        source="api",
        payload={"version": data.version, "triggered_by": data.triggered_by},
    )
    mark_deployed(session, release, source="api")
    return release


# --------------------------------------------------------------------------- health checks


@dataclass
class CheckResult:
    passed: bool
    http_status: int | None = None
    latency_ms: int | None = None
    error: str | None = None


def record_health_check(
    session: Session, release: Release, sequence: int, result: CheckResult
) -> None:
    """Store one check and decide whether the release is healthy, unhealthy or needs more checks."""
    if release.status != S.VERIFYING:
        # Rolled back or otherwise resolved while the check was in flight.
        return

    settings = get_settings()
    session.add(
        HealthCheck(
            release_id=release.id,
            sequence=sequence,
            outcome=CheckOutcome.PASSED if result.passed else CheckOutcome.FAILED,
            http_status=result.http_status,
            latency_ms=result.latency_ms,
            error=result.error,
            checked_at=utcnow(),
        )
    )
    session.flush()

    failures = session.scalar(
        select(func.count()).where(
            HealthCheck.release_id == release.id,
            HealthCheck.outcome == CheckOutcome.FAILED,
        )
    )

    if failures >= settings.health_check_failure_threshold:
        release.status = S.UNHEALTHY
        release.verified_at = utcnow()
        record_event(
            session,
            service_id=release.service_id,
            release_id=release.id,
            event_type="verification.failed",
            source="worker",
            payload={"failed_checks": failures, "last_error": result.error},
        )
        if release.service.auto_rollback:
            request_rollback(
                session,
                release,
                reason=f"Automatic: {failures} of {sequence} health checks failed",
                source="worker",
            )
    elif sequence >= release.checks_required:
        release.status = S.HEALTHY
        release.verified_at = utcnow()
        record_event(
            session,
            service_id=release.service_id,
            release_id=release.id,
            event_type="verification.passed",
            source="worker",
            payload={"checks": sequence, "failed_checks": failures},
        )
    else:
        enqueue(
            session,
            JOB_HEALTH_CHECK,
            {"release_id": release.id, "sequence": sequence + 1},
            delay_seconds=settings.health_check_interval_seconds,
        )


# --------------------------------------------------------------------------- rollback


def previous_healthy_release(session: Session, release: Release) -> Release | None:
    return session.scalar(
        select(Release)
        .where(
            Release.service_id == release.service_id,
            Release.environment == release.environment,
            Release.status == S.HEALTHY,
            Release.id != release.id,
            Release.created_at <= release.created_at,
        )
        .order_by(Release.created_at.desc(), Release.id.desc())
        .limit(1)
    )


def request_rollback(session: Session, release: Release, *, reason: str, source: str) -> None:
    if release.status not in ROLLBACK_FROM:
        raise TransitionError(f"cannot roll back a release that is {release.status}")

    target = previous_healthy_release(session, release)
    release.status = S.ROLLING_BACK
    release.rollback_reason = reason
    release.rollback_requested_at = utcnow()
    release.rollback_target_id = target.id if target else None
    record_event(
        session,
        service_id=release.service_id,
        release_id=release.id,
        event_type="rollback.requested",
        source=source,
        payload={
            "reason": reason,
            "target_release_id": target.id if target else None,
            "target_version": target.version if target else None,
        },
    )
    if release.service.rollback_webhook_url:
        enqueue(session, JOB_ROLLBACK_NOTIFY, {"release_id": release.id})


def complete_rollback(session: Session, release: Release, *, succeeded: bool) -> None:
    if succeeded:
        allowed = ROLLBACK_FROM | {S.ROLLING_BACK}
        if release.status not in allowed:
            raise TransitionError(f"cannot complete rollback of a release that is {release.status}")
        release.status = S.ROLLED_BACK
        release.rollback_completed_at = utcnow()
    else:
        if release.status != S.ROLLING_BACK:
            raise TransitionError(f"no rollback in progress (release is {release.status})")
        release.status = S.UNHEALTHY
