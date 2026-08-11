"""first-party curriculum metadata and stable item identity

Revision ID: 0013
Revises: 0012
Create Date: 2026-08-11

The migration is additive. It gives a versioned first-party curriculum stable
item keys, actionable external resources, read-only recall-card targets, and
advisory stretch work. None of these fields participate in Study Plan capacity,
progress, card scheduling, scoring, or SM-2.

Written by hand because autogenerate is deliberately disabled; see
docs/DEVIATIONS.md §9.
"""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision: str = "0013"
down_revision: str | None = "0012"
branch_labels = None
depends_on = None

JSON_TYPE = JSONB().with_variant(sa.JSON(), "sqlite")


def upgrade() -> None:
    empty_array = (
        sa.text("'[]'::jsonb")
        if op.get_bind().dialect.name == "postgresql"
        else sa.text("'[]'")
    )
    op.add_column(
        "study_plan_items",
        sa.Column("curriculum_key", sa.Text(), nullable=True),
    )
    op.add_column(
        "study_plan_items",
        sa.Column(
            "resources",
            JSON_TYPE,
            nullable=False,
            server_default=empty_array,
        ),
    )
    op.add_column(
        "study_plan_items",
        sa.Column(
            "mapped_recall_topics",
            JSON_TYPE,
            nullable=False,
            server_default=empty_array,
        ),
    )
    op.add_column(
        "study_plan_items",
        sa.Column(
            "stretch_actions",
            JSON_TYPE,
            nullable=False,
            server_default=empty_array,
        ),
    )
    op.create_index(
        "uq_study_plan_items_curriculum_key",
        "study_plan_items",
        ["plan_id", "curriculum_key"],
        unique=True,
        postgresql_where=sa.text("curriculum_key IS NOT NULL"),
        sqlite_where=sa.text("curriculum_key IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_study_plan_items_curriculum_key",
        table_name="study_plan_items",
    )
    op.drop_column("study_plan_items", "stretch_actions")
    op.drop_column("study_plan_items", "mapped_recall_topics")
    op.drop_column("study_plan_items", "resources")
    op.drop_column("study_plan_items", "curriculum_key")
