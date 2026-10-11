import json

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from releasewatch import ingest
from releasewatch.config import get_settings
from releasewatch.db import get_session
from releasewatch.schemas import CIWebhook, WebhookAccepted

router = APIRouter(prefix="/api/webhooks", tags=["webhooks"])


async def raw_body(request: Request) -> bytes:
    # The signature covers the exact bytes sent, so verify before JSON parsing.
    return await request.body()


@router.post("/ci", response_model=WebhookAccepted, status_code=status.HTTP_202_ACCEPTED)
def receive_ci_webhook(
    body: bytes = Depends(raw_body),
    x_releasewatch_signature: str | None = Header(default=None),
    x_releasewatch_delivery: str | None = Header(default=None, max_length=100),
    session: Session = Depends(get_session),
) -> WebhookAccepted:
    if not ingest.verify_signature(body, get_settings().webhook_secret, x_releasewatch_signature):
        raise HTTPException(status_code=401, detail="invalid signature")

    try:
        hook = CIWebhook.model_validate(json.loads(body))
    except (json.JSONDecodeError, ValidationError) as exc:
        detail = (
            exc.errors(include_url=False, include_context=False)
            if isinstance(exc, ValidationError)
            else "body is not valid JSON"
        )
        raise HTTPException(status_code=422, detail=detail) from exc

    # Two concurrent deliveries of the same webhook can both pass the duplicate
    # check; the unique constraints catch the loser, which then sees the winner's row.
    for attempt in range(2):
        try:
            result = ingest.ingest(session, hook, x_releasewatch_delivery)
            session.commit()
            break
        except IntegrityError:
            session.rollback()
            if attempt == 1:
                raise

    return WebhookAccepted(
        event_id=result.event.id,
        release_id=result.release.id if result.release else None,
        release_status=result.release.status if result.release else None,
        applied=result.applied,
        duplicate=result.duplicate,
        detail=result.detail,
    )
