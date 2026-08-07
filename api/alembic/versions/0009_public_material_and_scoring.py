"""public material workflow and universal scoring names

Revision ID: 0009
Revises: 0008
Create Date: 2026-08-07

The axis columns are renamed in place. No score, answer, schedule, or mastery
value is recomputed. Material import is proposal-first and owns no scheduler
state until the user confirms selected topics.
"""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

from alembic import op

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("onboarding_completed", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.execute("UPDATE users SET onboarding_completed = true WHERE is_founder = true")

    for table, old, new in (
        ("cards", "last_mechanism_accuracy", "last_accuracy"),
        ("cards", "last_trade_off_awareness", "last_depth"),
        ("cards", "last_failure_mode_awareness", "last_boundaries"),
        ("sessions", "mechanism_accuracy", "accuracy"),
        ("sessions", "trade_off_awareness", "depth"),
        ("sessions", "failure_mode_awareness", "boundaries"),
        ("sessions", "reattempt_mechanism_accuracy", "reattempt_accuracy"),
    ):
        op.alter_column(table, old, new_column_name=new)

    op.create_table(
        "material_sources",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "user_id",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("lineage_id", UUID(as_uuid=True), nullable=False),
        sa.Column(
            "previous_version_id",
            UUID(as_uuid=True),
            sa.ForeignKey("material_sources.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("kind", sa.Text(), nullable=False, server_default="guide"),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("source_text", sa.Text(), nullable=False),
        sa.Column("original_filename", sa.Text(), nullable=False, server_default=""),
        sa.Column("mime_type", sa.Text(), nullable=False, server_default="text/plain"),
        sa.Column("import_path", sa.Text(), nullable=False, server_default="topics"),
        sa.Column("intent", sa.Text(), nullable=False, server_default="already_studied"),
        sa.Column("status", sa.Text(), nullable=False, server_default="draft"),
        sa.Column("requested_weeks", sa.Integer(), nullable=False, server_default="12"),
        sa.Column("weekly_capacity_minutes", sa.Integer(), nullable=False, server_default="480"),
        sa.Column("mode", sa.Text(), nullable=False, server_default="flexible"),
        sa.Column("deadline", sa.Date(), nullable=True),
        sa.Column(
            "plan_draft_id",
            UUID(as_uuid=True),
            sa.ForeignKey("study_plan_guide_drafts.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("result_summary", JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("error", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('draft','pending','processing','ready','needs_attention',"
            "'failed','confirmed','superseded')",
            name="ck_material_sources_status",
        ),
        sa.CheckConstraint("import_path IN ('topics','plan')", name="ck_material_sources_path"),
        sa.CheckConstraint(
            "intent IN ('already_studied','learn')", name="ck_material_sources_intent"
        ),
        sa.CheckConstraint("version > 0", name="ck_material_sources_version"),
    )
    op.create_index("ix_material_sources_user_status", "material_sources", ["user_id", "status"])
    op.create_index(
        "uq_material_sources_version",
        "material_sources",
        ["user_id", "lineage_id", "version"],
        unique=True,
    )

    op.create_table(
        "material_topic_proposals",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "source_id",
            UUID(as_uuid=True),
            sa.ForeignKey("material_sources.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("section_title", sa.Text(), nullable=False, server_default=""),
        sa.Column("topic", sa.Text(), nullable=False),
        sa.Column("answer_anchor", sa.Text(), nullable=False),
        sa.Column("source_excerpt", sa.Text(), nullable=False, server_default=""),
        sa.Column("status", sa.Text(), nullable=False, server_default="clean"),
        sa.Column("issue", sa.Text(), nullable=False, server_default=""),
        sa.Column(
            "merged_into_id",
            UUID(as_uuid=True),
            sa.ForeignKey("material_topic_proposals.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('clean','needs_attention','excluded','confirmed')",
            name="ck_material_topic_proposals_status",
        ),
    )
    op.create_index(
        "ix_material_topic_proposals_source",
        "material_topic_proposals",
        ["source_id", "position"],
    )

    op.create_table(
        "llm_usage",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "user_id",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("operation", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_llm_usage_user_created", "llm_usage", ["user_id", "created_at"])

    op.add_column("cards", sa.Column("answer_anchor", sa.Text(), nullable=False, server_default=""))
    op.add_column(
        "cards", sa.Column("source_excerpt", sa.Text(), nullable=False, server_default="")
    )
    op.add_column("cards", sa.Column("source_id", UUID(as_uuid=True), nullable=True))
    op.create_foreign_key(
        "fk_cards_source_id_material_sources",
        "cards",
        "material_sources",
        ["source_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint("fk_cards_source_id_material_sources", "cards", type_="foreignkey")
    op.drop_column("cards", "source_id")
    op.drop_column("cards", "source_excerpt")
    op.drop_column("cards", "answer_anchor")
    op.drop_index("ix_llm_usage_user_created", table_name="llm_usage")
    op.drop_table("llm_usage")
    op.drop_index("ix_material_topic_proposals_source", table_name="material_topic_proposals")
    op.drop_table("material_topic_proposals")
    op.drop_index("uq_material_sources_version", table_name="material_sources")
    op.drop_index("ix_material_sources_user_status", table_name="material_sources")
    op.drop_table("material_sources")

    for table, old, new in (
        ("cards", "last_mechanism_accuracy", "last_accuracy"),
        ("cards", "last_trade_off_awareness", "last_depth"),
        ("cards", "last_failure_mode_awareness", "last_boundaries"),
        ("sessions", "mechanism_accuracy", "accuracy"),
        ("sessions", "trade_off_awareness", "depth"),
        ("sessions", "failure_mode_awareness", "boundaries"),
        ("sessions", "reattempt_mechanism_accuracy", "reattempt_accuracy"),
    ):
        op.alter_column(table, new, new_column_name=old)
    op.drop_column("users", "onboarding_completed")
