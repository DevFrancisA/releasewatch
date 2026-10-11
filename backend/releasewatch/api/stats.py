from datetime import timedelta

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from releasewatch.db import get_session
from releasewatch.lifecycle import utcnow
from releasewatch.models import Release, ReleaseStatus, Service
from releasewatch.schemas import ServiceStats, Summary

router = APIRouter(prefix="/api/stats", tags=["stats"])

# Releases that needed remediation count against the change failure rate.
FAILURE_STATUSES = {
    ReleaseStatus.FAILED,
    ReleaseStatus.UNHEALTHY,
    ReleaseStatus.ROLLING_BACK,
    ReleaseStatus.ROLLED_BACK,
}


def _rate(failed: int, total: int) -> float:
    return round(failed / total, 4) if total else 0.0


@router.get("/summary", response_model=Summary)
def summary(
    window_days: int = Query(default=7, ge=1, le=90),
    environment: str | None = None,
    session: Session = Depends(get_session),
) -> Summary:
    since = utcnow() - timedelta(days=window_days)

    services_stats: list[ServiceStats] = []
    by_status: dict[str, int] = {s.value: 0 for s in ReleaseStatus}
    for service in session.scalars(select(Service).order_by(Service.name)):
        query = select(Release).where(Release.service_id == service.id, Release.created_at >= since)
        if environment:
            query = query.where(Release.environment == environment)
        releases = session.scalars(query).all()
        for release in releases:
            by_status[release.status] += 1

        last = session.scalar(
            select(Release)
            .where(Release.service_id == service.id)
            .order_by(Release.created_at.desc(), Release.id.desc())
            .limit(1)
        )
        failed = sum(1 for r in releases if r.status in FAILURE_STATUSES)
        services_stats.append(
            ServiceStats(
                service=service.name,
                releases=len(releases),
                failed=failed,
                change_failure_rate=_rate(failed, len(releases)),
                last_release_status=last.status if last else None,
                last_release_version=last.version if last else None,
                last_release_at=last.created_at if last else None,
            )
        )

    total = sum(by_status.values())
    failed_total = sum(by_status[s] for s in FAILURE_STATUSES)
    return Summary(
        window_days=window_days,
        releases=total,
        by_status=by_status,
        change_failure_rate=_rate(failed_total, total),
        services=services_stats,
    )
