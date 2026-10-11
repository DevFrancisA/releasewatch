from collections.abc import Sequence

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from releasewatch.models import CheckOutcome, Event, HealthCheck, Release
from releasewatch.schemas import HealthSummary, ReleaseOut


def releases_out(session: Session, releases: Sequence[Release]) -> list[ReleaseOut]:
    """Serialize a page of releases with two aggregate queries instead of N+1 lookups.

    Callers must load `Release.service` eagerly (joinedload) so that reading the
    service name does not trigger a query per row.
    """
    ids = [r.id for r in releases]
    if not ids:
        return []

    health = {
        row.release_id: (row.passed, row.failed)
        for row in session.execute(
            select(
                HealthCheck.release_id,
                func.count(case((HealthCheck.outcome == CheckOutcome.PASSED, 1))).label("passed"),
                func.count(case((HealthCheck.outcome == CheckOutcome.FAILED, 1))).label("failed"),
            )
            .where(HealthCheck.release_id.in_(ids))
            .group_by(HealthCheck.release_id)
        )
    }
    event_counts = {
        release_id: count
        for release_id, count in session.execute(
            select(Event.release_id, func.count())
            .where(Event.release_id.in_(ids))
            .group_by(Event.release_id)
        )
    }
    return [
        to_out(release, health.get(release.id, (0, 0)), event_counts.get(release.id, 0))
        for release in releases
    ]


def release_out(session: Session, release: Release) -> ReleaseOut:
    return releases_out(session, [release])[0]


def to_out(release: Release, health: tuple[int, int], event_count: int) -> ReleaseOut:
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
        health=HealthSummary(passed=health[0], failed=health[1], required=release.checks_required),
        event_count=event_count,
    )
