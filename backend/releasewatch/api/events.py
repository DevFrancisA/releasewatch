from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from releasewatch.db import get_session
from releasewatch.models import Event, Service
from releasewatch.schemas import EventOut, EventPage

router = APIRouter(prefix="/api/events", tags=["events"])


@router.get("", response_model=EventPage)
def list_events(
    service: str | None = None,
    release_id: int | None = None,
    event_type: str | None = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    session: Session = Depends(get_session),
) -> EventPage:
    query = select(Event)
    if service:
        query = query.join(Service, Service.id == Event.service_id).where(Service.name == service)
    if release_id is not None:
        query = query.where(Event.release_id == release_id)
    if event_type:
        query = query.where(Event.event_type == event_type)

    total = session.scalar(select(func.count()).select_from(query.subquery())) or 0
    events = session.scalars(
        query.order_by(Event.received_at.desc(), Event.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()
    return EventPage(
        items=[EventOut.model_validate(e) for e in events],
        page=page,
        page_size=page_size,
        total=total,
    )
