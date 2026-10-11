from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from releasewatch.db import get_session
from releasewatch.models import Event, Service
from releasewatch.pagination import paginate, split_page
from releasewatch.schemas import EventOut, EventPage

router = APIRouter(prefix="/api/events", tags=["events"])


@router.get("", response_model=EventPage)
def list_events(
    service: str | None = None,
    release_id: int | None = None,
    event_type: str | None = None,
    cursor: str | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    session: Session = Depends(get_session),
) -> EventPage:
    query = select(Event)
    if service:
        service_id = session.scalar(select(Service.id).where(Service.name == service))
        if service_id is None:
            return EventPage(items=[], next_cursor=None)
        query = query.where(Event.service_id == service_id)
    if release_id is not None:
        query = query.where(Event.release_id == release_id)
    if event_type:
        query = query.where(Event.event_type == event_type)

    rows = session.scalars(paginate(query, Event.received_at, Event.id, cursor, limit)).all()
    events, next_cursor = split_page(list(rows), limit, lambda e: (e.received_at, e.id))
    return EventPage(
        items=[EventOut.model_validate(e) for e in events],
        next_cursor=next_cursor,
    )
