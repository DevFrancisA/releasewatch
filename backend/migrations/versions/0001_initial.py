"""initial schema

Revision ID: 0001
Revises:
Create Date: 2026-10-11
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TZ = sa.DateTime(timezone=True)


def upgrade() -> None:
    op.create_table(
        "services",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(100), nullable=False, unique=True),
        sa.Column("repository", sa.String(300)),
        sa.Column("default_health_url", sa.String(500)),
        sa.Column("auto_rollback", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("rollback_webhook_url", sa.String(500)),
        sa.Column("created_at", TZ, nullable=False, server_default=sa.func.now()),
    )
    op.create_table(
        "releases",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "service_id",
            sa.Integer(),
            sa.ForeignKey("services.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("environment", sa.String(50), nullable=False),
        sa.Column("version", sa.String(100), nullable=False),
        sa.Column("external_id", sa.String(200), nullable=False),
        sa.Column("commit_sha", sa.String(64)),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("health_url", sa.String(500)),
        sa.Column("ci_run_url", sa.String(500)),
        sa.Column("triggered_by", sa.String(100)),
        sa.Column("checks_required", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", TZ, nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", TZ, nullable=False, server_default=sa.func.now()),
        sa.Column("deployed_at", TZ),
        sa.Column("verified_at", TZ),
        sa.Column(
            "rollback_target_id",
            sa.Integer(),
            sa.ForeignKey("releases.id", ondelete="SET NULL"),
        ),
        sa.Column("rollback_reason", sa.Text()),
        sa.Column("rollback_requested_at", TZ),
        sa.Column("rollback_completed_at", TZ),
        sa.UniqueConstraint("service_id", "external_id", name="uq_releases_external"),
    )
    op.create_table(
        "events",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column(
            "service_id",
            sa.Integer(),
            sa.ForeignKey("services.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("release_id", sa.Integer(), sa.ForeignKey("releases.id", ondelete="CASCADE")),
        sa.Column("event_type", sa.String(50), nullable=False),
        sa.Column("source", sa.String(20), nullable=False),
        sa.Column("delivery_id", sa.String(100), unique=True),
        sa.Column(
            "payload",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("received_at", TZ, nullable=False, server_default=sa.func.now()),
    )
    op.create_table(
        "health_checks",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "release_id",
            sa.Integer(),
            sa.ForeignKey("releases.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("outcome", sa.String(10), nullable=False),
        sa.Column("http_status", sa.Integer()),
        sa.Column("latency_ms", sa.Integer()),
        sa.Column("error", sa.Text()),
        sa.Column("checked_at", TZ, nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("release_id", "sequence", name="uq_health_checks_seq"),
    )
    op.create_table(
        "jobs",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("kind", sa.String(50), nullable=False),
        sa.Column(
            "payload",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("status", sa.String(10), nullable=False),
        sa.Column("run_at", TZ, nullable=False, server_default=sa.func.now()),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_error", sa.Text()),
        sa.Column("locked_at", TZ),
        sa.Column("created_at", TZ, nullable=False, server_default=sa.func.now()),
        sa.Column("finished_at", TZ),
    )


def downgrade() -> None:
    for table in ("jobs", "health_checks", "events", "releases", "services"):
        op.drop_table(table)
