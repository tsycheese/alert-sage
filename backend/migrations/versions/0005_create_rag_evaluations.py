"""create rag evaluation runs and results

Revision ID: 0005
Revises: 0004
Create Date: 2026-07-26
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "rag_evaluation_runs",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("idempotency_key", sa.String(length=160), nullable=False),
        sa.Column("evaluation_set_id", sa.String(length=80), nullable=False),
        sa.Column("evaluation_set_version", sa.String(length=40), nullable=False),
        sa.Column("evaluation_set_sha256", sa.String(length=64), nullable=False),
        sa.Column("build_revision", sa.String(length=64), nullable=True),
        sa.Column("provider", sa.String(length=24), nullable=False),
        sa.Column("dataset_id", sa.String(length=64), nullable=True),
        sa.Column("split", sa.String(length=16), nullable=False),
        sa.Column("top_k", sa.Integer(), nullable=False),
        sa.Column("score_threshold", sa.Float(), nullable=True),
        sa.Column(
            "status",
            sa.String(length=16),
            server_default="queued",
            nullable=False,
        ),
        sa.Column("attempt", sa.Integer(), server_default="0", nullable=False),
        sa.Column("query_count", sa.Integer(), nullable=False),
        sa.Column("completed_query_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("summary_metrics", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
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
        sa.CheckConstraint(
            "attempt >= 0", name=op.f("ck_rag_evaluation_runs_attempt_non_negative")
        ),
        sa.CheckConstraint(
            "completed_query_count >= 0 AND completed_query_count <= query_count",
            name=op.f("ck_rag_evaluation_runs_completed_query_count_range"),
        ),
        sa.CheckConstraint(
            "provider IN ('mock', 'dify')",
            name=op.f("ck_rag_evaluation_runs_provider_values"),
        ),
        sa.CheckConstraint(
            "query_count >= 0",
            name=op.f("ck_rag_evaluation_runs_query_count_non_negative"),
        ),
        sa.CheckConstraint(
            "score_threshold IS NULL OR (score_threshold >= 0 AND score_threshold <= 1)",
            name=op.f("ck_rag_evaluation_runs_score_threshold_range"),
        ),
        sa.CheckConstraint(
            "split IN ('calibration', 'test')",
            name=op.f("ck_rag_evaluation_runs_split_values"),
        ),
        sa.CheckConstraint(
            "status IN ('queued', 'running', 'completed', 'failed')",
            name=op.f("ck_rag_evaluation_runs_status_values"),
        ),
        sa.CheckConstraint(
            "top_k BETWEEN 1 AND 10",
            name=op.f("ck_rag_evaluation_runs_top_k_range"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_rag_evaluation_runs")),
        sa.UniqueConstraint("idempotency_key", name="rag_evaluation_runs_idempotency_key"),
    )
    op.create_index(
        "ix_rag_evaluation_runs_status_created",
        "rag_evaluation_runs",
        ["status", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_rag_evaluation_runs_set_version",
        "rag_evaluation_runs",
        ["evaluation_set_id", "evaluation_set_version"],
        unique=False,
    )
    op.create_table(
        "rag_evaluation_results",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("run_id", sa.UUID(), nullable=False),
        sa.Column("query_id", sa.String(length=80), nullable=False),
        sa.Column("split", sa.String(length=16), nullable=False),
        sa.Column("difficulty", sa.String(length=16), nullable=False),
        sa.Column("query_text", sa.String(length=250), nullable=False),
        sa.Column("ground_truth", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("retrieved_items", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("source_hit_at_k", sa.Boolean(), nullable=True),
        sa.Column("recall_at_k", sa.Float(), nullable=True),
        sa.Column("reciprocal_rank", sa.Float(), nullable=True),
        sa.Column("abstention_correct", sa.Boolean(), nullable=True),
        sa.Column("false_positive", sa.Boolean(), nullable=True),
        sa.Column("latency_ms", sa.Float(), nullable=False),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "difficulty IN ('easy', 'medium', 'hard')",
            name=op.f("ck_rag_evaluation_results_difficulty_values"),
        ),
        sa.CheckConstraint(
            "latency_ms >= 0",
            name=op.f("ck_rag_evaluation_results_latency_non_negative"),
        ),
        sa.CheckConstraint(
            "recall_at_k IS NULL OR (recall_at_k >= 0 AND recall_at_k <= 1)",
            name=op.f("ck_rag_evaluation_results_recall_range"),
        ),
        sa.CheckConstraint(
            "reciprocal_rank IS NULL OR (reciprocal_rank >= 0 AND reciprocal_rank <= 1)",
            name=op.f("ck_rag_evaluation_results_reciprocal_rank_range"),
        ),
        sa.CheckConstraint(
            "split IN ('calibration', 'test')",
            name=op.f("ck_rag_evaluation_results_split_values"),
        ),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["rag_evaluation_runs.id"],
            name=op.f("fk_rag_evaluation_results_run_id_rag_evaluation_runs"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_rag_evaluation_results")),
        sa.UniqueConstraint("run_id", "query_id", name="rag_evaluation_results_run_query"),
    )
    op.create_index(
        "ix_rag_evaluation_results_run_id",
        "rag_evaluation_results",
        ["run_id"],
        unique=False,
    )

    op.drop_constraint(
        op.f("ck_outbox_messages_topic_values"),
        "outbox_messages",
        type_="check",
    )
    op.create_check_constraint(
        op.f("ck_outbox_messages_topic_values"),
        "outbox_messages",
        "topic IN ('workflow.start', 'workflow.resume', 'workflow.retry', 'case.sync', "
        "'rag.evaluation.run')",
    )


def downgrade() -> None:
    op.drop_constraint(
        op.f("ck_outbox_messages_topic_values"),
        "outbox_messages",
        type_="check",
    )
    op.create_check_constraint(
        op.f("ck_outbox_messages_topic_values"),
        "outbox_messages",
        "topic IN ('workflow.start', 'workflow.resume', 'workflow.retry', 'case.sync')",
    )
    op.drop_index("ix_rag_evaluation_results_run_id", table_name="rag_evaluation_results")
    op.drop_table("rag_evaluation_results")
    op.drop_index("ix_rag_evaluation_runs_set_version", table_name="rag_evaluation_runs")
    op.drop_index("ix_rag_evaluation_runs_status_created", table_name="rag_evaluation_runs")
    op.drop_table("rag_evaluation_runs")
