from __future__ import annotations

import enum
from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from releasewatch.db import Base


class ReleaseStatus(enum.StrEnum):
    DEPLOYING = "deploying"
    VERIFYING = "verifying"
    HEALTHY = "healthy"
    UNHEALTHY = "unhealthy"
    FAILED = "failed"
    ROLLING_BACK = "rolling_back"
    ROLLED_BACK = "rolled_back"


class CheckOutcome(enum.StrEnum):
    PASSED = "passed"
    FAILED = "failed"


class JobStatus(enum.StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"


class Service(Base):
    __tablename__ = "services"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True)
    repository: Mapped[str | None] = mapped_column(String(300))
    default_health_url: Mapped[str | None] = mapped_column(String(500))
    auto_rollback: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    # When set, ReleaseWatch POSTs rollback requests here so CI can act on them.
    rollback_webhook_url: Mapped[str | None] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    releases: Mapped[list[Release]] = relationship(back_populates="service")


class Release(Base):
    __tablename__ = "releases"
    # Lists are ordered by (created_at DESC, id DESC); Postgres scans these btrees
    # backwards for that, so each filter + sort combination is a single range scan.
    __table_args__ = (
        UniqueConstraint("service_id", "external_id", name="uq_releases_external"),
        Index("ix_releases_created", "created_at", "id"),
        Index("ix_releases_service_created", "service_id", "created_at", "id"),
        Index("ix_releases_service_env_created", "service_id", "environment", "created_at", "id"),
        Index("ix_releases_status_created", "status", "created_at", "id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    service_id: Mapped[int] = mapped_column(ForeignKey("services.id", ondelete="CASCADE"))
    environment: Mapped[str] = mapped_column(String(50))
    version: Mapped[str] = mapped_column(String(100))
    # CI-side identifier used to correlate the webhooks of a single deployment.
    external_id: Mapped[str] = mapped_column(String(200))
    commit_sha: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(20))
    health_url: Mapped[str | None] = mapped_column(String(500))
    ci_run_url: Mapped[str | None] = mapped_column(String(500))
    triggered_by: Mapped[str | None] = mapped_column(String(100))
    checks_required: Mapped[int] = mapped_column(Integer, default=0, server_default="0")

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    deployed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    rollback_target_id: Mapped[int | None] = mapped_column(
        ForeignKey("releases.id", ondelete="SET NULL")
    )
    rollback_reason: Mapped[str | None] = mapped_column(Text)
    rollback_requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    rollback_completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    service: Mapped[Service] = relationship(back_populates="releases")
    health_checks: Mapped[list[HealthCheck]] = relationship(
        back_populates="release", order_by="HealthCheck.sequence"
    )
    events: Mapped[list[Event]] = relationship(
        back_populates="release", order_by="Event.received_at"
    )
    rollback_target: Mapped[Release | None] = relationship(remote_side=[id])


class Event(Base):
    """An append-only record of everything that happened to a release.

    Inbound CI webhooks and internal lifecycle transitions (health check
    verdicts, rollback requests) are both stored here, which gives the UI a
    single timeline per release.
    """

    __tablename__ = "events"
    __table_args__ = (
        Index("ix_events_release_received", "release_id", "received_at", "id"),
        Index("ix_events_service_received", "service_id", "received_at", "id"),
        Index("ix_events_received", "received_at", "id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    service_id: Mapped[int] = mapped_column(ForeignKey("services.id", ondelete="CASCADE"))
    release_id: Mapped[int | None] = mapped_column(ForeignKey("releases.id", ondelete="CASCADE"))
    event_type: Mapped[str] = mapped_column(String(50))
    source: Mapped[str] = mapped_column(String(20))
    # Webhook delivery id; makes redelivered webhooks idempotent.
    delivery_id: Mapped[str | None] = mapped_column(String(100), unique=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default="{}")
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    release: Mapped[Release | None] = relationship(back_populates="events")
    service: Mapped[Service] = relationship()


class HealthCheck(Base):
    __tablename__ = "health_checks"
    __table_args__ = (UniqueConstraint("release_id", "sequence", name="uq_health_checks_seq"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    release_id: Mapped[int] = mapped_column(ForeignKey("releases.id", ondelete="CASCADE"))
    sequence: Mapped[int] = mapped_column(Integer)
    outcome: Mapped[str] = mapped_column(String(10))
    http_status: Mapped[int | None] = mapped_column(Integer)
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    error: Mapped[str | None] = mapped_column(Text)
    checked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    release: Mapped[Release] = relationship(back_populates="health_checks")


class Job(Base):
    """Postgres-backed work queue consumed by the worker with SKIP LOCKED."""

    __tablename__ = "jobs"
    __table_args__ = (
        # The worker's claim query only ever looks at due, queued jobs.
        Index("ix_jobs_queued_run_at", "run_at", "id", postgresql_where=text("status = 'queued'")),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    kind: Mapped[str] = mapped_column(String(50))
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default="{}")
    status: Mapped[str] = mapped_column(String(10), default=JobStatus.QUEUED)
    run_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    attempts: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    last_error: Mapped[str | None] = mapped_column(Text)
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
