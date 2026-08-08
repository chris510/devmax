"""grounded captures and recoverable card lifecycle

Revision ID: 0008
Revises: 0007
Create Date: 2026-08-07

Written by hand because SQLModel metadata intentionally is not Alembic's
autogenerate authority in this project.
"""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

from alembic import op

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("cards", sa.Column("source_url", sa.Text(), nullable=False, server_default=""))
    op.add_column(
        "cards", sa.Column("source_section", sa.Text(), nullable=False, server_default="")
    )
    op.add_column(
        "cards", sa.Column("source_label", sa.Text(), nullable=False, server_default="")
    )
    op.add_column(
        "cards", sa.Column("answer_basis", sa.Text(), nullable=False, server_default="")
    )
    op.add_column(
        "cards",
        sa.Column(
            "answer_rubric",
            JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )
    op.add_column(
        "cards",
        sa.Column("lifecycle_status", sa.Text(), nullable=False, server_default="active"),
    )
    op.add_column("cards", sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("cards", sa.Column("replaces_card_id", UUID(as_uuid=True), nullable=True))
    op.add_column("cards", sa.Column("replaced_by_card_id", UUID(as_uuid=True), nullable=True))
    op.create_foreign_key(
        "fk_cards_replaces_card",
        "cards",
        "cards",
        ["replaces_card_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_cards_replaced_by_card",
        "cards",
        "cards",
        ["replaced_by_card_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_check_constraint(
        "ck_cards_lifecycle_status",
        "cards",
        "lifecycle_status IN ('active', 'archived')",
    )

    # Replace the old hot index with the actual active-queue predicate. Keeping
    # both would make every schedule write maintain an index no query uses.
    op.drop_index("ix_cards_due_conversational", table_name="cards")
    op.create_index(
        "ix_cards_active_due_conversational",
        "cards",
        ["next_review_at"],
        postgresql_where=sa.text(
            "delivery_mode = 'conversational' AND lifecycle_status = 'active'"
        ),
    )

    op.create_table(
        "pending_captures",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("topic", sa.Text(), nullable=False),
        sa.Column("context", sa.Text(), nullable=False, server_default=""),
        sa.Column("status", sa.Text(), nullable=False, server_default="pending_source"),
        sa.Column("source_url", sa.Text(), nullable=False, server_default=""),
        sa.Column("source_section", sa.Text(), nullable=False, server_default=""),
        sa.Column("source_label", sa.Text(), nullable=False, server_default=""),
        sa.Column("answer_basis", sa.Text(), nullable=False, server_default=""),
        sa.Column(
            "answer_rubric",
            JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("canonical_question", sa.Text(), nullable=False, server_default=""),
        sa.Column(
            "activated_card_id",
            UUID(as_uuid=True),
            sa.ForeignKey("cards.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "status IN ('pending_source', 'ready_to_review', 'activated')",
            name="ck_pending_captures_status",
        ),
        sa.CheckConstraint(
            "(status = 'activated') = (activated_card_id IS NOT NULL)",
            name="ck_pending_captures_activation",
        ),
    )
    op.create_index(
        "ix_pending_captures_created", "pending_captures", ["created_at"]
    )

    op.add_column(
        "study_plan_items",
        sa.Column("recall_supported", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column(
        "study_plan_card_proposals",
        sa.Column("source_label", sa.Text(), nullable=False, server_default=""),
    )
    op.add_column(
        "study_plan_card_proposals",
        sa.Column("answer_basis", sa.Text(), nullable=False, server_default=""),
    )
    op.add_column(
        "study_plan_card_proposals",
        sa.Column(
            "answer_rubric",
            JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )


def downgrade() -> None:
    op.drop_column("study_plan_card_proposals", "answer_rubric")
    op.drop_column("study_plan_card_proposals", "answer_basis")
    op.drop_column("study_plan_card_proposals", "source_label")
    op.drop_column("study_plan_items", "recall_supported")

    op.drop_index("ix_pending_captures_created", table_name="pending_captures")
    op.drop_table("pending_captures")

    op.drop_index("ix_cards_active_due_conversational", table_name="cards")
    op.create_index(
        "ix_cards_due_conversational",
        "cards",
        ["next_review_at"],
        postgresql_where=sa.text("delivery_mode = 'conversational'"),
    )
    op.drop_constraint("ck_cards_lifecycle_status", "cards", type_="check")
    op.drop_constraint("fk_cards_replaced_by_card", "cards", type_="foreignkey")
    op.drop_constraint("fk_cards_replaces_card", "cards", type_="foreignkey")
    op.drop_column("cards", "replaced_by_card_id")
    op.drop_column("cards", "replaces_card_id")
    op.drop_column("cards", "archived_at")
    op.drop_column("cards", "lifecycle_status")
    op.drop_column("cards", "answer_rubric")
    op.drop_column("cards", "answer_basis")
    op.drop_column("cards", "source_label")
    op.drop_column("cards", "source_section")
    op.drop_column("cards", "source_url")
