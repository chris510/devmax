"""remember engaged pushes after missed-review evaluation

Revision ID: 0025
Revises: 0024
Create Date: 2026-08-15

The nullable watermark is filled lazily only when check-missed proves engagement.
No historical push is guessed or reclassified during migration.
"""

import sqlalchemy as sa

from alembic import op

revision: str = "0025"
down_revision: str | None = "0024"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "cards",
        sa.Column("push_resolved_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("cards", "push_resolved_at")
