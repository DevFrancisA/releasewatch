from datetime import timedelta

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
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

    # One grouped scan for per-service, per-status counts inside the window.
    counts_query = (
        select(Release.service_id, Release.status, func.count())
        .where(Release.created_at >= since)
        .group_by(Release.service_id, Release.status)
    )
    # One DISTINCT ON query for each service's most recent release.
    latest_query = (
        select(Release.service_id, Release.status, Release.version, Release.created_at)
        .distinct(Release.service_id)
        .order_by(Release.service_id, Release.created_at.desc(), Release.id.desc())
    )
    if environment:
        counts_query = counts_query.where(Release.environment == environment)
        latest_query = latest_query.where(Release.environment == environment)

    by_status: dict[str, int] = {s.value: 0 for s in ReleaseStatus}
    per_service: dict[int, list[int]] = {}
    for service_id, status, count in session.execute(counts_query):
        by_status[status] += count
        totals = per_service.setdefault(service_id, [0, 0])
        totals[0] += count
        if status in FAILURE_STATUSES:
            totals[1] += count

    latest = {row.service_id: row for row in session.execute(latest_query)}

    services_stats = []
    services = session.execute(select(Service.id, Service.name).order_by(Service.name))
    for service_id, name in services:
        total, failed = per_service.get(service_id, (0, 0))
        last = latest.get(service_id)
        services_stats.append(
            ServiceStats(
                service=name,
                releases=total,
                failed=failed,
                change_failure_rate=_rate(failed, total),
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
