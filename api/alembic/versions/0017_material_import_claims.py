"""bind each long guide import to one recoverable worker claim

Revision ID: 0017
Revises: 0016
Create Date: 2026-08-13

The columns are coordination metadata only.  Existing material and Study Plan
draft rows receive null claims, so their content and status are unchanged.  An
existing material ``processing`` row is therefore treated as an orphan from
the pre-claim implementation.
"""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

from alembic import op

revision: str = "0017"
down_revision: str | None = "0016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "material_sources",
        sa.Column("processing_run_id", UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "material_sources",
        sa.Column(
            "processing_heartbeat_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )
    op.add_column(
        "study_plan_guide_drafts",
        sa.Column("processing_run_id", UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "study_plan_guide_drafts",
        sa.Column(
            "processing_heartbeat_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("study_plan_guide_drafts", "processing_heartbeat_at")
    op.drop_column("study_plan_guide_drafts", "processing_run_id")
    op.drop_column("material_sources", "processing_heartbeat_at")
    op.drop_column("material_sources", "processing_run_id")
