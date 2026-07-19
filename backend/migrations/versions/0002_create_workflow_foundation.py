"""Create durable workflow foundation tables.

Revision ID: 0002
Revises: 0001
Create Date: 2026-07-19
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "workflow_runs",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("alert_id", sa.UUID(), nullable=False),
        sa.Column("thread_id", sa.String(length=128), nullable=False),
        sa.Column("idempotency_key", sa.String(length=160), nullable=False),
        sa.Column("workflow_version", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), server_default="queued", nullable=False),
        sa.Column("current_node", sa.String(length=64), nullable=True),
        sa.Column("attempt", sa.Integer(), server_default="1", nullable=False),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.CheckConstraint("attempt >= 1", name=op.f("ck_workflow_runs_attempt_positive")),
        sa.CheckConstraint(
            "status IN ('queued', 'running', 'waiting_for_approval', 'reanalyzing', "
            "'completed', 'rejected', 'failed')",
            name=op.f("ck_workflow_runs_status_values"),
        ),
        sa.CheckConstraint(
            "finished_at IS NULL OR started_at IS NULL OR finished_at >= started_at",
            name=op.f("ck_workflow_runs_valid_time_range"),
        ),
        sa.ForeignKeyConstraint(
            ["alert_id"],
            ["alerts.id"],
            name=op.f("fk_workflow_runs_alert_id_alerts"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_workflow_runs")),
        sa.UniqueConstraint("idempotency_key", name="workflow_runs_idempotency_key"),
        sa.UniqueConstraint("thread_id", name="workflow_runs_thread_id"),
    )
    op.create_index(
        "ix_workflow_runs_alert_created",
        "workflow_runs",
        ["alert_id", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_workflow_runs_status_updated",
        "workflow_runs",
        ["status", "updated_at"],
        unique=False,
    )
    op.create_index(
        "uq_workflow_runs_alert_active",
        "workflow_runs",
        ["alert_id"],
        unique=True,
        postgresql_where=sa.text(
            "status IN ('queued', 'running', 'waiting_for_approval', 'reanalyzing')"
        ),
    )

    op.create_table(
        "workflow_events",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("workflow_run_id", sa.UUID(), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("idempotency_key", sa.String(length=160), nullable=False),
        sa.Column("event_type", sa.String(length=48), nullable=False),
        sa.Column("node_name", sa.String(length=64), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=True),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "occurred_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "event_type IN ('workflow_queued', 'workflow_started', 'node_started', "
            "'node_completed', 'node_failed', 'tool_started', 'tool_completed', "
            "'tool_failed', 'human_input_required', 'human_decision_received', "
            "'workflow_completed', 'workflow_rejected', 'workflow_failed')",
            name=op.f("ck_workflow_events_event_type_values"),
        ),
        sa.CheckConstraint("sequence >= 1", name=op.f("ck_workflow_events_sequence_positive")),
        sa.ForeignKeyConstraint(
            ["workflow_run_id"],
            ["workflow_runs.id"],
            name=op.f("fk_workflow_events_workflow_run_id_workflow_runs"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_workflow_events")),
        sa.UniqueConstraint(
            "workflow_run_id",
            "idempotency_key",
            name="workflow_events_run_idempotency_key",
        ),
        sa.UniqueConstraint("workflow_run_id", "sequence", name="workflow_events_run_sequence"),
    )
    op.create_index(
        "ix_workflow_events_run_occurred",
        "workflow_events",
        ["workflow_run_id", "occurred_at"],
        unique=False,
    )

    op.create_table(
        "tool_executions",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("workflow_run_id", sa.UUID(), nullable=False),
        sa.Column("node_name", sa.String(length=64), nullable=False),
        sa.Column("tool_name", sa.String(length=64), nullable=False),
        sa.Column("idempotency_key", sa.String(length=160), nullable=False),
        sa.Column("status", sa.String(length=24), server_default="pending", nullable=False),
        sa.Column("attempt", sa.Integer(), server_default="1", nullable=False),
        sa.Column("input", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("output", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.CheckConstraint("attempt >= 1", name=op.f("ck_tool_executions_attempt_positive")),
        sa.CheckConstraint(
            "duration_ms IS NULL OR duration_ms >= 0",
            name=op.f("ck_tool_executions_duration_non_negative"),
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'running', 'succeeded', 'failed', 'timed_out', 'skipped')",
            name=op.f("ck_tool_executions_status_values"),
        ),
        sa.CheckConstraint(
            "finished_at IS NULL OR started_at IS NULL OR finished_at >= started_at",
            name=op.f("ck_tool_executions_valid_time_range"),
        ),
        sa.ForeignKeyConstraint(
            ["workflow_run_id"],
            ["workflow_runs.id"],
            name=op.f("fk_tool_executions_workflow_run_id_workflow_runs"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_tool_executions")),
        sa.UniqueConstraint(
            "workflow_run_id",
            "idempotency_key",
            name="tool_executions_run_idempotency_key",
        ),
    )
    op.create_index(
        "ix_tool_executions_run_tool",
        "tool_executions",
        ["workflow_run_id", "tool_name"],
        unique=False,
    )
    op.create_index(
        "ix_tool_executions_status_updated",
        "tool_executions",
        ["status", "updated_at"],
        unique=False,
    )

    op.create_table(
        "diagnosis_reports",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("workflow_run_id", sa.UUID(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("schema_version", sa.String(length=32), server_default="1.0", nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("root_causes", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("evidence", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("recommendations", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("confidence", sa.Numeric(precision=5, scale=4), nullable=False),
        sa.Column("model_name", sa.String(length=128), nullable=False),
        sa.Column("prompt_version", sa.String(length=64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "confidence >= 0 AND confidence <= 1",
            name=op.f("ck_diagnosis_reports_confidence_range"),
        ),
        sa.CheckConstraint("version >= 1", name=op.f("ck_diagnosis_reports_version_positive")),
        sa.ForeignKeyConstraint(
            ["workflow_run_id"],
            ["workflow_runs.id"],
            name=op.f("fk_diagnosis_reports_workflow_run_id_workflow_runs"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_diagnosis_reports")),
        sa.UniqueConstraint("workflow_run_id", "version", name="diagnosis_reports_run_version"),
    )
    op.create_index(
        "ix_diagnosis_reports_run_created",
        "diagnosis_reports",
        ["workflow_run_id", "created_at"],
        unique=False,
    )

    op.create_table(
        "human_decisions",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("diagnosis_report_id", sa.UUID(), nullable=False),
        sa.Column("idempotency_key", sa.String(length=160), nullable=False),
        sa.Column("action", sa.String(length=24), nullable=False),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column("actor", sa.String(length=128), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "action IN ('approve', 'reject', 'reanalyze')",
            name=op.f("ck_human_decisions_action_values"),
        ),
        sa.CheckConstraint(
            "action <> 'reanalyze' OR (comment IS NOT NULL AND btrim(comment) <> '')",
            name=op.f("ck_human_decisions_reanalyze_comment_required"),
        ),
        sa.ForeignKeyConstraint(
            ["diagnosis_report_id"],
            ["diagnosis_reports.id"],
            name=op.f("fk_human_decisions_diagnosis_report_id_diagnosis_reports"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_human_decisions")),
        sa.UniqueConstraint("diagnosis_report_id", name="human_decisions_diagnosis_report"),
        sa.UniqueConstraint("idempotency_key", name="human_decisions_idempotency_key"),
    )
    op.create_index(
        "ix_human_decisions_created_at",
        "human_decisions",
        ["created_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_human_decisions_created_at", table_name="human_decisions")
    op.drop_table("human_decisions")
    op.drop_index("ix_diagnosis_reports_run_created", table_name="diagnosis_reports")
    op.drop_table("diagnosis_reports")
    op.drop_index("ix_tool_executions_status_updated", table_name="tool_executions")
    op.drop_index("ix_tool_executions_run_tool", table_name="tool_executions")
    op.drop_table("tool_executions")
    op.drop_index("ix_workflow_events_run_occurred", table_name="workflow_events")
    op.drop_table("workflow_events")
    op.drop_index("uq_workflow_runs_alert_active", table_name="workflow_runs")
    op.drop_index("ix_workflow_runs_status_updated", table_name="workflow_runs")
    op.drop_index("ix_workflow_runs_alert_created", table_name="workflow_runs")
    op.drop_table("workflow_runs")
