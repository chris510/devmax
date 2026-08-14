"""add source-grounded adaptive lesson artifacts

Revision ID: 0018
Revises: 0017
Create Date: 2026-08-14

The lesson workflow extends the existing material -> proposal -> card pipeline.
No card score, session, mastery, or SM-2 column is added or rewritten. Existing
guide/manual rows receive empty additive defaults and keep their current path.
"""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

from alembic import op

revision: str = "0018"
down_revision: str | None = "0017"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint("ck_material_sources_path", "material_sources", type_="check")
    op.create_check_constraint(
        "ck_material_sources_path",
        "material_sources",
        "import_path IN ('topics','plan','lesson')",
    )

    op.add_column(
        "material_sources",
        sa.Column("source_url", sa.Text(), nullable=False, server_default=""),
    )
    op.add_column(
        "material_sources",
        sa.Column("canonical_note_markdown", sa.Text(), nullable=False, server_default=""),
    )
    op.add_column(
        "material_sources",
        sa.Column("recall_export_markdown", sa.Text(), nullable=False, server_default=""),
    )
    op.add_column(
        "material_sources",
        sa.Column("distilled_at", sa.DateTime(timezone=True), nullable=True),
    )

    op.add_column(
        "material_topic_proposals",
        sa.Column("canonical_question", sa.Text(), nullable=False, server_default=""),
    )
    op.add_column(
        "material_topic_proposals",
        sa.Column(
            "answer_rubric",
            JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )
    op.add_column(
        "material_topic_proposals",
        sa.Column(
            "recall_questions",
            JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )
    op.add_column(
        "material_topic_proposals",
        sa.Column("card_id", UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_material_topic_proposals_card_id_cards",
        "material_topic_proposals",
        "cards",
        ["card_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "uq_material_topic_proposals_card_id",
        "material_topic_proposals",
        ["card_id"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index(
        "uq_material_topic_proposals_card_id",
        table_name="material_topic_proposals",
    )
    op.drop_constraint(
        "fk_material_topic_proposals_card_id_cards",
        "material_topic_proposals",
        type_="foreignkey",
    )
    op.drop_column("material_topic_proposals", "card_id")
    op.drop_column("material_topic_proposals", "recall_questions")
    op.drop_column("material_topic_proposals", "answer_rubric")
    op.drop_column("material_topic_proposals", "canonical_question")

    op.drop_column("material_sources", "distilled_at")
    op.drop_column("material_sources", "recall_export_markdown")
    op.drop_column("material_sources", "canonical_note_markdown")
    op.drop_column("material_sources", "source_url")

    op.drop_constraint("ck_material_sources_path", "material_sources", type_="check")
    # 0017 has no lesson path. Preserve the source/proposals as a legacy topics
    # import rather than making the downgrade impossible once a lesson exists.
    op.execute("UPDATE material_sources SET import_path = 'topics' WHERE import_path = 'lesson'")
    op.create_check_constraint(
        "ck_material_sources_path",
        "material_sources",
        "import_path IN ('topics','plan')",
    )
