from sqlalchemy import func, select
from sqlalchemy.orm import Session

from releasewatch.models import CheckOutcome, Event, Release
from releasewatch.schemas import HealthSummary, ReleaseOut


def release_out(session: Session, release: Release) -> ReleaseOut:
    checks = release.health_checks
    event_count = session.scalar(
        select(func.count()).select_from(Event).where(Event.release_id == release.id)
    )
    return ReleaseOut(
        id=release.id,
        service=release.service.name,
        environment=release.environment,
        version=release.version,
        commit_sha=release.commit_sha,
        status=release.status,
        health_url=release.health_url,
        ci_run_url=release.ci_run_url,
        triggered_by=release.triggered_by,
        created_at=release.created_at,
        deployed_at=release.deployed_at,
        verified_at=release.verified_at,
        rollback_target_id=release.rollback_target_id,
        rollback_reason=release.rollback_reason,
        rollback_requested_at=release.rollback_requested_at,
        rollback_completed_at=release.rollback_completed_at,
        health=HealthSummary(
            passed=sum(1 for c in checks if c.outcome == CheckOutcome.PASSED),
            failed=sum(1 for c in checks if c.outcome == CheckOutcome.FAILED),
            required=release.checks_required,
        ),
        event_count=event_count or 0,
    )
