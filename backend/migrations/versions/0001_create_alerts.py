"""Create alerts table.

Revision ID: 0001
Revises:
Create Date: 2026-07-18
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "alerts",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("external_alert_id", sa.String(length=128), nullable=False),
        sa.Column("fingerprint", sa.String(length=128), nullable=True),
        sa.Column("alert_name", sa.String(length=128), nullable=False),
        sa.Column("service", sa.String(length=128), nullable=False),
        sa.Column("instance", sa.String(length=128), nullable=True),
        sa.Column("severity", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "severity IN ('info', 'warning', 'critical')",
            name=op.f("ck_alerts_severity_values"),
        ),
        sa.CheckConstraint(
            "status IN ('received', 'running', 'waiting_for_approval', 'reanalyzing', "
            "'completed', 'rejected', 'failed')",
            name=op.f("ck_alerts_status_values"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_alerts")),
        sa.UniqueConstraint(
            "source",
            "external_alert_id",
            name="source_external_alert_id",
        ),
    )
    op.create_index("ix_alerts_created_at_desc", "alerts", ["created_at"], unique=False)
    op.create_index(op.f("ix_alerts_fingerprint"), "alerts", ["fingerprint"], unique=False)
    op.create_index(op.f("ix_alerts_service"), "alerts", ["service"], unique=False)
    op.create_index(op.f("ix_alerts_severity"), "alerts", ["severity"], unique=False)
    op.create_index(op.f("ix_alerts_status"), "alerts", ["status"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_alerts_status"), table_name="alerts")
    op.drop_index(op.f("ix_alerts_severity"), table_name="alerts")
    op.drop_index(op.f("ix_alerts_service"), table_name="alerts")
    op.drop_index(op.f("ix_alerts_fingerprint"), table_name="alerts")
    op.drop_index("ix_alerts_created_at_desc", table_name="alerts")
    op.drop_table("alerts")
