"""composite indexes for list, filter, and timeline queries

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-11
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

INDEXES = [
    ("ix_releases_created", "releases", ["created_at", "id"]),
    ("ix_releases_service_created", "releases", ["service_id", "created_at", "id"]),
    (
        "ix_releases_service_env_created",
        "releases",
        ["service_id", "environment", "created_at", "id"],
    ),
    ("ix_releases_status_created", "releases", ["status", "created_at", "id"]),
    ("ix_events_release_received", "events", ["release_id", "received_at", "id"]),
    ("ix_events_service_received", "events", ["service_id", "received_at", "id"]),
    ("ix_events_received", "events", ["received_at", "id"]),
]


def upgrade() -> None:
    for name, table, columns in INDEXES:
        op.create_index(name, table, columns)
    op.create_index(
        "ix_jobs_queued_run_at",
        "jobs",
        ["run_at", "id"],
        postgresql_where=sa.text("status = 'queued'"),
    )


def downgrade() -> None:
    op.drop_index("ix_jobs_queued_run_at", table_name="jobs")
    for name, table, _ in reversed(INDEXES):
        op.drop_index(name, table_name=table)
