from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload, selectinload

from releasewatch import lifecycle
from releasewatch.api.serializers import release_out, releases_out, to_out
from releasewatch.db import get_session
from releasewatch.lifecycle import NewRelease, TransitionError
from releasewatch.models import CheckOutcome, Release, ReleaseStatus, Service
from releasewatch.pagination import paginate, split_page
from releasewatch.schemas import (
    EventOut,
    HealthCheckOut,
    ReleaseCreate,
    ReleaseDetail,
    ReleaseOut,
    ReleasePage,
    RollbackRequest,
)

router = APIRouter(prefix="/api/releases", tags=["releases"])


@router.get("", response_model=ReleasePage)
def list_releases(
    service: str | None = None,
    environment: str | None = None,
    status_: ReleaseStatus | None = Query(default=None, alias="status"),
    cursor: str | None = None,
    limit: int = Query(default=25, ge=1, le=100),
    session: Session = Depends(get_session),
) -> ReleasePage:
    query = select(Release).options(joinedload(Release.service))
    if service:
        service_id = session.scalar(select(Service.id).where(Service.name == service))
        if service_id is None:
            return ReleasePage(items=[], next_cursor=None)
        query = query.where(Release.service_id == service_id)
    if environment:
        query = query.where(Release.environment == environment)
    if status_:
        query = query.where(Release.status == status_)

    rows = session.scalars(paginate(query, Release.created_at, Release.id, cursor, limit)).all()
    releases, next_cursor = split_page(list(rows), limit, lambda r: (r.created_at, r.id))
    return ReleasePage(items=releases_out(session, releases), next_cursor=next_cursor)


@router.post("", response_model=ReleaseOut, status_code=status.HTTP_201_CREATED)
def create_release(body: ReleaseCreate, session: Session = Depends(get_session)) -> ReleaseOut:
    release = lifecycle.register_manual_release(
        session,
        NewRelease(
            service=body.service,
            environment=body.environment,
            version=body.version,
            commit_sha=body.commit_sha,
            health_url=body.health_url,
            triggered_by=body.triggered_by,
        ),
    )
    session.commit()
    return release_out(session, release)


def _get_release(session: Session, release_id: int, *, lock: bool = False) -> Release:
    release = session.get(Release, release_id, with_for_update=lock)
    if release is None:
        raise HTTPException(status_code=404, detail="release not found")
    return release


@router.get("/{release_id}", response_model=ReleaseDetail)
def get_release(release_id: int, session: Session = Depends(get_session)) -> ReleaseDetail:
    release = session.scalar(
        select(Release)
        .where(Release.id == release_id)
        .options(
            joinedload(Release.service),
            joinedload(Release.rollback_target),
            selectinload(Release.health_checks),
            selectinload(Release.events),
        )
    )
    if release is None:
        raise HTTPException(status_code=404, detail="release not found")
    checks = release.health_checks
    return ReleaseDetail(
        **to_out(
            release,
            health=(
                sum(c.outcome == CheckOutcome.PASSED for c in checks),
                sum(c.outcome == CheckOutcome.FAILED for c in checks),
            ),
            event_count=len(release.events),
        ).model_dump(),
        rollback_target_version=release.rollback_target.version
        if release.rollback_target
        else None,
        health_checks=[HealthCheckOut.model_validate(c) for c in checks],
        events=[EventOut.model_validate(e) for e in release.events],
    )


@router.post("/{release_id}/rollback", response_model=ReleaseOut)
def rollback_release(
    release_id: int, body: RollbackRequest, session: Session = Depends(get_session)
) -> ReleaseOut:
    release = _get_release(session, release_id, lock=True)
    try:
        lifecycle.request_rollback(session, release, reason=body.reason, source="api")
    except TransitionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    session.commit()
    return release_out(session, release)
