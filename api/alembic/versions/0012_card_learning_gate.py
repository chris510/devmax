"""record answer-authority exposure without changing SM-2

Revision ID: 0012
Revises: 0011
Create Date: 2026-08-11

Written by hand because autogenerate is deliberately disabled. Null means no
learning hold, so this migration preserves every existing due queue, session,
score, mastery summary, and SM-2 field exactly.
"""

import sqlalchemy as sa

from alembic import op

revision: str = "0012"
down_revision: str | None = "0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "cards",
        sa.Column("last_learning_exposure_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "cards",
        sa.Column("recall_not_before_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("cards", "recall_not_before_at")
    op.drop_column("cards", "last_learning_exposure_at")
