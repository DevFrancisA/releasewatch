from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from releasewatch import lifecycle
from releasewatch.api.serializers import release_out
from releasewatch.db import get_session
from releasewatch.lifecycle import NewRelease, TransitionError
from releasewatch.models import Release, ReleaseStatus, Service
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
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=25, ge=1, le=100),
    session: Session = Depends(get_session),
) -> ReleasePage:
    query = select(Release)
    if service:
        query = query.join(Service).where(Service.name == service)
    if environment:
        query = query.where(Release.environment == environment)
    if status_:
        query = query.where(Release.status == status_)

    total = session.scalar(select(func.count()).select_from(query.subquery())) or 0
    releases = session.scalars(
        query.order_by(Release.created_at.desc(), Release.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()
    return ReleasePage(
        items=[release_out(session, r) for r in releases],
        page=page,
        page_size=page_size,
        total=total,
    )


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
    release = _get_release(session, release_id)
    base = release_out(session, release)
    return ReleaseDetail(
        **base.model_dump(),
        rollback_target_version=release.rollback_target.version
        if release.rollback_target
        else None,
        health_checks=[HealthCheckOut.model_validate(c) for c in release.health_checks],
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
