from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import AfterValidator, AnyHttpUrl, BaseModel, ConfigDict, Field, TypeAdapter

NAME_PATTERN = r"^[a-z0-9][a-z0-9._-]{0,99}$"

CIEventType = Literal[
    "deployment.started",
    "deployment.succeeded",
    "deployment.failed",
    "rollback.completed",
    "rollback.failed",
]


_http_url = TypeAdapter(AnyHttpUrl)


def _validate_http_url(value: str) -> str:
    return str(_http_url.validate_python(value))


HttpUrl = Annotated[str, Field(max_length=500), AfterValidator(_validate_http_url)]


class CIWebhook(BaseModel):
    """Payload CI sends to POST /api/webhooks/ci."""

    event: CIEventType
    service: str = Field(pattern=NAME_PATTERN)
    environment: str = Field(default="production", pattern=NAME_PATTERN)
    version: str = Field(min_length=1, max_length=100)
    deployment_id: str | None = Field(default=None, max_length=200)
    commit_sha: str | None = Field(default=None, max_length=64)
    health_url: HttpUrl | None = None
    ci_run_url: HttpUrl | None = None
    actor: str | None = Field(default=None, max_length=100)


class WebhookAccepted(BaseModel):
    event_id: int
    release_id: int | None
    release_status: str | None
    applied: bool
    duplicate: bool
    detail: str | None = None


class ReleaseCreate(BaseModel):
    service: str = Field(pattern=NAME_PATTERN)
    environment: str = Field(default="production", pattern=NAME_PATTERN)
    version: str = Field(min_length=1, max_length=100)
    commit_sha: str | None = Field(default=None, max_length=64)
    health_url: HttpUrl | None = None
    triggered_by: str | None = Field(default=None, max_length=100)


class RollbackRequest(BaseModel):
    reason: str = Field(min_length=1, max_length=500)


class ServiceCreate(BaseModel):
    name: str = Field(pattern=NAME_PATTERN)
    repository: str | None = Field(default=None, max_length=300)
    default_health_url: HttpUrl | None = None
    auto_rollback: bool = False
    rollback_webhook_url: HttpUrl | None = None


class ServiceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    repository: str | None
    default_health_url: str | None
    auto_rollback: bool
    rollback_webhook_url: str | None
    created_at: datetime


class HealthSummary(BaseModel):
    passed: int
    failed: int
    required: int


class ReleaseOut(BaseModel):
    id: int
    service: str
    environment: str
    version: str
    commit_sha: str | None
    status: str
    health_url: str | None
    ci_run_url: str | None
    triggered_by: str | None
    created_at: datetime
    deployed_at: datetime | None
    verified_at: datetime | None
    rollback_target_id: int | None
    rollback_reason: str | None
    rollback_requested_at: datetime | None
    rollback_completed_at: datetime | None
    health: HealthSummary
    event_count: int


class HealthCheckOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    sequence: int
    outcome: str
    http_status: int | None
    latency_ms: int | None
    error: str | None
    checked_at: datetime


class EventOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    service_id: int
    release_id: int | None
    event_type: str
    source: str
    payload: dict[str, Any]
    received_at: datetime


class ReleaseDetail(ReleaseOut):
    rollback_target_version: str | None
    health_checks: list[HealthCheckOut]
    events: list[EventOut]


class ReleasePage(BaseModel):
    items: list[ReleaseOut]
    page: int
    page_size: int
    total: int


class EventPage(BaseModel):
    items: list[EventOut]
    page: int
    page_size: int
    total: int


class ServiceStats(BaseModel):
    service: str
    releases: int
    failed: int
    change_failure_rate: float
    last_release_status: str | None
    last_release_version: str | None
    last_release_at: datetime | None


class Summary(BaseModel):
    window_days: int
    releases: int
    by_status: dict[str, int]
    change_failure_rate: float
    services: list[ServiceStats]
