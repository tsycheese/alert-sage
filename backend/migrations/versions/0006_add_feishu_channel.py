"""add Feishu interaction channel and auditable actor identity

Revision ID: 0006
Revises: 0005
Create Date: 2026-08-09
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "human_decisions",
        sa.Column("actor_source", sa.String(length=16), server_default="web", nullable=False),
    )
    op.add_column(
        "human_decisions", sa.Column("actor_subject", sa.String(length=128), nullable=True)
    )
    op.add_column(
        "human_decisions", sa.Column("actor_display_name", sa.String(length=128), nullable=True)
    )
    op.create_check_constraint(
        op.f("ck_human_decisions_actor_source_values"),
        "human_decisions",
        "actor_source IN ('web', 'feishu')",
    )
    op.create_check_constraint(
        op.f("ck_human_decisions_comment_max_length"),
        "human_decisions",
        "comment IS NULL OR char_length(comment) <= 1000",
    )
    op.execute(
        "UPDATE human_decisions SET actor_subject = actor, actor_display_name = actor "
        "WHERE actor_subject IS NULL"
    )

    op.create_table(
        "feishu_card_bindings",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("alert_id", sa.UUID(), nullable=False),
        sa.Column("app_id", sa.String(length=64), nullable=False),
        sa.Column("chat_id", sa.String(length=128), nullable=False),
        sa.Column("message_id", sa.String(length=128), nullable=True),
        sa.Column("desired_revision", sa.Integer(), server_default="1", nullable=False),
        sa.Column("delivered_revision", sa.Integer(), server_default="0", nullable=False),
        sa.Column("action_nonce_hash", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=16), server_default="pending", nullable=False),
        sa.Column("last_error_code", sa.String(length=64), nullable=True),
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
            "desired_revision >= 1", name=op.f("ck_feishu_card_bindings_desired_revision_positive")
        ),
        sa.CheckConstraint(
            "delivered_revision >= 0 AND delivered_revision <= desired_revision",
            name=op.f("ck_feishu_card_bindings_delivered_revision_range"),
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'active', 'failed', 'replaced')",
            name=op.f("ck_feishu_card_bindings_status_values"),
        ),
        sa.ForeignKeyConstraint(
            ["alert_id"],
            ["alerts.id"],
            name=op.f("fk_feishu_card_bindings_alert_id_alerts"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_feishu_card_bindings")),
        sa.UniqueConstraint("alert_id", name="feishu_card_bindings_alert_id"),
    )
    op.create_index(
        "ix_feishu_card_bindings_status_updated",
        "feishu_card_bindings",
        ["status", "updated_at"],
        unique=False,
    )

    op.create_table(
        "feishu_callback_events",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("app_id", sa.String(length=64), nullable=False),
        sa.Column("event_id", sa.String(length=128), nullable=False),
        sa.Column("binding_id", sa.UUID(), nullable=True),
        sa.Column("open_id", sa.String(length=128), nullable=True),
        sa.Column("action", sa.String(length=32), nullable=True),
        sa.Column("message_id", sa.String(length=128), nullable=True),
        sa.Column("chat_id", sa.String(length=128), nullable=True),
        sa.Column("request_timestamp", sa.BigInteger(), nullable=False),
        sa.Column("raw_body_sha256", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=16), server_default="received", nullable=False),
        sa.Column("result_code", sa.String(length=64), nullable=True),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column(
            "response_payload",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "received_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('received', 'processed', 'rejected', 'failed')",
            name=op.f("ck_feishu_callback_events_status_values"),
        ),
        sa.ForeignKeyConstraint(
            ["binding_id"],
            ["feishu_card_bindings.id"],
            name=op.f("fk_feishu_callback_events_binding_id_feishu_card_bindings"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_feishu_callback_events")),
        sa.UniqueConstraint("app_id", "event_id", name="feishu_callback_events_app_event"),
    )
    op.create_index(
        "ix_feishu_callback_events_received",
        "feishu_callback_events",
        ["received_at"],
        unique=False,
    )
    op.create_index(
        "ix_feishu_callback_events_binding",
        "feishu_callback_events",
        ["binding_id", "received_at"],
        unique=False,
    )

    op.create_table(
        "feishu_deliveries",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("binding_id", sa.UUID(), nullable=False),
        sa.Column("kind", sa.String(length=24), nullable=False),
        sa.Column("recipient_open_id", sa.String(length=128), nullable=True),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("action_nonce", sa.String(length=128), nullable=False),
        sa.Column("idempotency_key", sa.String(length=200), nullable=False),
        sa.Column("status", sa.String(length=16), server_default="pending", nullable=False),
        sa.Column("attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column("message_id", sa.String(length=128), nullable=True),
        sa.Column("last_error_code", sa.String(length=64), nullable=True),
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
        sa.CheckConstraint("revision >= 1", name=op.f("ck_feishu_deliveries_revision_positive")),
        sa.CheckConstraint(
            "attempts >= 0", name=op.f("ck_feishu_deliveries_attempts_non_negative")
        ),
        sa.CheckConstraint(
            "kind IN ('card_sync', 'private_reminder')",
            name=op.f("ck_feishu_deliveries_kind_values"),
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'processing', 'succeeded', 'failed')",
            name=op.f("ck_feishu_deliveries_status_values"),
        ),
        sa.ForeignKeyConstraint(
            ["binding_id"],
            ["feishu_card_bindings.id"],
            name=op.f("fk_feishu_deliveries_binding_id_feishu_card_bindings"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_feishu_deliveries")),
        sa.UniqueConstraint("idempotency_key", name="feishu_deliveries_idempotency_key"),
    )
    op.create_index(
        "ix_feishu_deliveries_status_updated",
        "feishu_deliveries",
        ["status", "updated_at"],
        unique=False,
    )
    op.create_index(
        "ix_feishu_deliveries_binding_revision",
        "feishu_deliveries",
        ["binding_id", "revision"],
        unique=False,
    )

    op.drop_constraint(op.f("ck_outbox_messages_topic_values"), "outbox_messages", type_="check")
    op.create_check_constraint(
        op.f("ck_outbox_messages_topic_values"),
        "outbox_messages",
        "topic IN ('workflow.start', 'workflow.resume', 'workflow.retry', 'case.sync', "
        "'rag.evaluation.run', 'feishu.card.sync', 'feishu.reminder.send')",
    )


def downgrade() -> None:
    op.drop_constraint(op.f("ck_outbox_messages_topic_values"), "outbox_messages", type_="check")
    op.create_check_constraint(
        op.f("ck_outbox_messages_topic_values"),
        "outbox_messages",
        "topic IN ('workflow.start', 'workflow.resume', 'workflow.retry', 'case.sync', "
        "'rag.evaluation.run')",
    )
    op.drop_index("ix_feishu_deliveries_binding_revision", table_name="feishu_deliveries")
    op.drop_index("ix_feishu_deliveries_status_updated", table_name="feishu_deliveries")
    op.drop_table("feishu_deliveries")
    op.drop_index("ix_feishu_callback_events_binding", table_name="feishu_callback_events")
    op.drop_index("ix_feishu_callback_events_received", table_name="feishu_callback_events")
    op.drop_table("feishu_callback_events")
    op.drop_index("ix_feishu_card_bindings_status_updated", table_name="feishu_card_bindings")
    op.drop_table("feishu_card_bindings")
    op.drop_constraint(
        op.f("ck_human_decisions_comment_max_length"), "human_decisions", type_="check"
    )
    op.drop_constraint(
        op.f("ck_human_decisions_actor_source_values"), "human_decisions", type_="check"
    )
    op.drop_column("human_decisions", "actor_display_name")
    op.drop_column("human_decisions", "actor_subject")
    op.drop_column("human_decisions", "actor_source")
