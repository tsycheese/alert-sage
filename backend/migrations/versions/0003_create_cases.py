"""Create case persistence and knowledge sync state.

Revision ID: 0003
Revises: 0002
Create Date: 2026-07-19
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

OLD_EVENT_TYPES = (
    "'workflow_queued', 'workflow_started', 'node_started', 'node_completed', "
    "'node_failed', 'tool_started', 'tool_completed', 'tool_failed', "
    "'human_input_required', 'human_decision_received', 'workflow_completed', "
    "'workflow_rejected', 'workflow_failed'"
)
NEW_EVENT_TYPES = (
    f"{OLD_EVENT_TYPES}, 'case_created', 'case_sync_started', "
    "'case_sync_succeeded', 'case_sync_failed'"
)


def upgrade() -> None:
    op.drop_constraint(
        op.f("ck_workflow_events_event_type_values"),
        "workflow_events",
        type_="check",
    )
    op.create_check_constraint(
        op.f("ck_workflow_events_event_type_values"),
        "workflow_events",
        f"event_type IN ({NEW_EVENT_TYPES})",
    )
    op.create_table(
        "cases",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("diagnosis_report_id", sa.UUID(), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("symptom", sa.Text(), nullable=False),
        sa.Column("root_cause", sa.Text(), nullable=False),
        sa.Column("resolution", sa.Text(), nullable=False),
        sa.Column("evidence", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("tags", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "knowledge_sync_status",
            sa.String(length=24),
            server_default="pending",
            nullable=False,
        ),
        sa.Column("knowledge_sync_attempt", sa.Integer(), server_default="0", nullable=False),
        sa.Column("external_document_id", sa.String(length=255), nullable=True),
        sa.Column("sync_error_code", sa.String(length=64), nullable=True),
        sa.Column("sync_error_message", sa.Text(), nullable=True),
        sa.Column("synced_at", sa.DateTime(timezone=True), nullable=True),
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
            "knowledge_sync_attempt >= 0",
            name=op.f("ck_cases_sync_attempt_non_negative"),
        ),
        sa.CheckConstraint(
            "knowledge_sync_status IN ('pending', 'syncing', 'synced', 'failed')",
            name=op.f("ck_cases_knowledge_sync_status_values"),
        ),
        sa.ForeignKeyConstraint(
            ["diagnosis_report_id"],
            ["diagnosis_reports.id"],
            name=op.f("fk_cases_diagnosis_report_id_diagnosis_reports"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_cases")),
        sa.UniqueConstraint("diagnosis_report_id", name="cases_diagnosis_report"),
    )
    op.create_index("ix_cases_created_at", "cases", ["created_at"], unique=False)
    op.create_index(
        "ix_cases_sync_status_updated",
        "cases",
        ["knowledge_sync_status", "updated_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_cases_sync_status_updated", table_name="cases")
    op.drop_index("ix_cases_created_at", table_name="cases")
    op.drop_table("cases")
    op.drop_constraint(
        op.f("ck_workflow_events_event_type_values"),
        "workflow_events",
        type_="check",
    )
    op.create_check_constraint(
        op.f("ck_workflow_events_event_type_values"),
        "workflow_events",
        f"event_type IN ({OLD_EVENT_TYPES})",
    )
