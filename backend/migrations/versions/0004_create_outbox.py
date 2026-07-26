"""Create the transactional outbox and backfill active delivery intents.

Revision ID: 0004
Revises: 0003
Create Date: 2026-07-26
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "outbox_messages",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("topic", sa.String(length=48), nullable=False),
        sa.Column("aggregate_id", sa.UUID(), nullable=False),
        sa.Column("idempotency_key", sa.String(length=200), nullable=False),
        sa.Column(
            "payload",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column(
            "correlation",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.String(length=16),
            server_default="pending",
            nullable=False,
        ),
        sa.Column("attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column(
            "available_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error_type", sa.String(length=128), nullable=True),
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
            "topic IN ('workflow.start', 'workflow.resume', 'workflow.retry', 'case.sync')",
            name=op.f("ck_outbox_messages_topic_values"),
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'published')",
            name=op.f("ck_outbox_messages_status_values"),
        ),
        sa.CheckConstraint(
            "attempts >= 0",
            name=op.f("ck_outbox_messages_attempts_non_negative"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_outbox_messages")),
        sa.UniqueConstraint(
            "idempotency_key",
            name="outbox_messages_idempotency_key",
        ),
    )
    op.create_index(
        "ix_outbox_messages_pending_available",
        "outbox_messages",
        ["available_at", "created_at"],
        unique=False,
        postgresql_where=sa.text("status = 'pending'"),
    )
    op.create_index(
        "ix_outbox_messages_aggregate",
        "outbox_messages",
        ["topic", "aggregate_id"],
        unique=False,
    )

    op.execute(
        """
        INSERT INTO outbox_messages (
            id, topic, aggregate_id, idempotency_key, payload, correlation
        )
        SELECT
            gen_random_uuid(),
            CASE WHEN attempt = 1 THEN 'workflow.start' ELSE 'workflow.retry' END,
            id,
            CASE
                WHEN attempt = 1 THEN 'workflow:start:' || id::text
                ELSE 'workflow:retry:' || id::text || ':' || 'attempt-' || attempt::text
            END,
            jsonb_build_object('workflow_run_id', id::text),
            '{}'::jsonb
        FROM workflow_runs
        WHERE status = 'queued'
        ON CONFLICT (idempotency_key) DO NOTHING
        """
    )
    op.execute(
        """
        INSERT INTO outbox_messages (
            id, topic, aggregate_id, idempotency_key, payload, correlation
        )
        SELECT
            gen_random_uuid(),
            'workflow.resume',
            workflow_runs.id,
            'workflow:resume:' || human_decisions.id::text,
            jsonb_build_object(
                'workflow_run_id', workflow_runs.id::text,
                'decision_id', human_decisions.id::text
            ),
            '{}'::jsonb
        FROM human_decisions
        JOIN diagnosis_reports
          ON diagnosis_reports.id = human_decisions.diagnosis_report_id
        JOIN workflow_runs
          ON workflow_runs.id = diagnosis_reports.workflow_run_id
        WHERE workflow_runs.status IN ('waiting_for_approval', 'reanalyzing')
        ON CONFLICT (idempotency_key) DO NOTHING
        """
    )
    op.execute(
        """
        INSERT INTO outbox_messages (
            id, topic, aggregate_id, idempotency_key, payload, correlation
        )
        SELECT
            gen_random_uuid(),
            'case.sync',
            id,
            'case:sync:' || id::text || ':' || 'delivery-1',
            jsonb_build_object('case_id', id::text),
            '{}'::jsonb
        FROM cases
        WHERE knowledge_sync_status = 'pending'
        ON CONFLICT (idempotency_key) DO NOTHING
        """
    )


def downgrade() -> None:
    op.drop_index("ix_outbox_messages_aggregate", table_name="outbox_messages")
    op.drop_index("ix_outbox_messages_pending_available", table_name="outbox_messages")
    op.drop_table("outbox_messages")
