from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from releasewatch.db import get_session
from releasewatch.models import Service
from releasewatch.schemas import ServiceCreate, ServiceOut

router = APIRouter(prefix="/api/services", tags=["services"])


@router.get("", response_model=list[ServiceOut])
def list_services(session: Session = Depends(get_session)) -> list[Service]:
    return list(session.scalars(select(Service).order_by(Service.name)))


@router.put("/{name}", response_model=ServiceOut)
def upsert_service(
    name: str, body: ServiceCreate, session: Session = Depends(get_session)
) -> Service:
    """Create or update a service's settings (health URL, auto-rollback, rollback hook)."""
    if body.name != name:
        raise HTTPException(status_code=422, detail="name in body must match the URL")
    service = session.scalar(select(Service).where(Service.name == name))
    if service is None:
        service = Service(name=name)
        session.add(service)
    service.repository = body.repository
    service.default_health_url = body.default_health_url
    service.auto_rollback = body.auto_rollback
    service.rollback_webhook_url = body.rollback_webhook_url
    session.commit()
    return service


@router.get("/{name}", response_model=ServiceOut)
def get_service(name: str, session: Session = Depends(get_session)) -> Service:
    service = session.scalar(select(Service).where(Service.name == name))
    if service is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="service not found")
    return service
