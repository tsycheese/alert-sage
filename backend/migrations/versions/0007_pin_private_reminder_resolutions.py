"""pin private reminder resolutions to human decisions

Revision ID: 0007
Revises: 0006
Create Date: 2026-08-11
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("feishu_deliveries", sa.Column("decision_id", sa.UUID(), nullable=True))
    op.create_foreign_key(
        op.f("fk_feishu_deliveries_decision_id_human_decisions"),
        "feishu_deliveries",
        "human_decisions",
        ["decision_id"],
        ["id"],
    )


def downgrade() -> None:
    op.drop_constraint(
        op.f("fk_feishu_deliveries_decision_id_human_decisions"),
        "feishu_deliveries",
        type_="foreignkey",
    )
    op.drop_column("feishu_deliveries", "decision_id")
