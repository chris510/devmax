"""durable Practice Debrief records

Revision ID: 0007
Revises: 0006
Create Date: 2026-08-07

Written by hand because SQLModel metadata intentionally is not Alembic's
autogenerate authority in this project.
"""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "study_plan_practice_debriefs",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "plan_id",
            UUID(as_uuid=True),
            sa.ForeignKey("study_plans.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "plan_item_id",
            UUID(as_uuid=True),
            sa.ForeignKey("study_plan_items.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("draft_text", sa.Text(), nullable=False, server_default=""),
        sa.Column("text", sa.Text(), nullable=False, server_default=""),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "(submitted_at IS NULL) OR (length(trim(text)) > 0)",
            name="ck_study_plan_practice_debriefs_submitted_text",
        ),
    )
    op.create_index(
        "uq_study_plan_practice_debriefs_item",
        "study_plan_practice_debriefs",
        ["plan_item_id"],
        unique=True,
    )
    op.create_index(
        "ix_study_plan_practice_debriefs_plan",
        "study_plan_practice_debriefs",
        ["plan_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_study_plan_practice_debriefs_plan",
        table_name="study_plan_practice_debriefs",
    )
    op.drop_index(
        "uq_study_plan_practice_debriefs_item",
        table_name="study_plan_practice_debriefs",
    )
    op.drop_table("study_plan_practice_debriefs")
