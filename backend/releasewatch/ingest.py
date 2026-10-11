"""CI webhook ingestion."""

from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from releasewatch import lifecycle
from releasewatch.lifecycle import NewRelease, TransitionError
from releasewatch.models import Event, Release, ReleaseStatus
from releasewatch.schemas import CIWebhook

S = ReleaseStatus

SIGNATURE_PREFIX = "sha256="
DEPLOY_EVENTS = ("deployment.started", "deployment.succeeded", "deployment.failed")


def sign(body: bytes, secret: str) -> str:
    digest = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return SIGNATURE_PREFIX + digest


def verify_signature(body: bytes, secret: str, header: str | None) -> bool:
    if not header or not header.startswith(SIGNATURE_PREFIX):
        return False
    return hmac.compare_digest(sign(body, secret), header)


@dataclass
class IngestResult:
    event: Event
    release: Release | None
    applied: bool
    duplicate: bool
    detail: str | None = None


def ingest(session: Session, hook: CIWebhook, delivery_id: str | None) -> IngestResult:
    if delivery_id:
        existing = session.scalar(select(Event).where(Event.delivery_id == delivery_id))
        if existing is not None:
            return IngestResult(existing, existing.release, applied=False, duplicate=True)

    service = lifecycle.get_or_create_service(session, hook.service)
    external_id = hook.deployment_id or f"{hook.environment}:{hook.version}"
    release = session.scalar(
        select(Release).where(Release.service_id == service.id, Release.external_id == external_id)
    )

    is_deploy_event = hook.event in DEPLOY_EVENTS
    if release is None and is_deploy_event:
        release = lifecycle.create_release(
            session,
            NewRelease(
                service=hook.service,
                environment=hook.environment,
                version=hook.version,
                external_id=external_id,
                commit_sha=hook.commit_sha,
                health_url=hook.health_url,
                ci_run_url=hook.ci_run_url,
                triggered_by=hook.actor,
            ),
            status=S.DEPLOYING,
            source="ci",
        )
    elif release is not None and release.status == S.DEPLOYING:
        # CI often only knows the health URL or run link by the time the deploy finishes.
        release.health_url = hook.health_url or release.health_url
        release.commit_sha = hook.commit_sha or release.commit_sha
        release.ci_run_url = hook.ci_run_url or release.ci_run_url
        release.triggered_by = hook.actor or release.triggered_by

    # Record the inbound event before applying it so the timeline stays in causal order.
    event = lifecycle.record_event(
        session,
        service_id=service.id,
        release_id=release.id if release else None,
        event_type=hook.event,
        source="ci",
        payload=hook.model_dump(mode="json", exclude_none=True),
        delivery_id=delivery_id,
    )

    applied, detail = True, None
    if release is None:
        applied, detail = False, "no release matches this deployment"
    elif is_deploy_event:
        if release.status != S.DEPLOYING:
            applied, detail = False, f"release already {release.status}"
        elif hook.event == "deployment.succeeded":
            lifecycle.mark_deployed(session, release, source="ci")
        elif hook.event == "deployment.failed":
            release.status = S.FAILED
    else:
        try:
            lifecycle.complete_rollback(
                session, release, succeeded=hook.event == "rollback.completed"
            )
        except TransitionError as exc:
            applied, detail = False, str(exc)

    session.flush()
    return IngestResult(event, release, applied=applied, duplicate=False, detail=detail)
