"""study plan card proposals, atomic acceptances, card links, duplications

Revision ID: 0006
Revises: 0005
Create Date: 2026-07-29

Written by hand — `alembic revision --autogenerate` is disabled on purpose
(`target_metadata = None`); see docs/DEVIATIONS.md §9.

No backfill, and — importantly — **no column is added to `cards`**. The link
between a plan item and a card it produced lives in `study_plan_card_links`.
That keeps "Study Plan never modifies an existing card" structural: dropping
this whole revision removes the links and leaves every card, score, session,
mastery summary, and SM-2 field byte-identical.

`uq_study_plan_acceptance_key` is what makes acceptance idempotent. A retry
after commit finds the committed row and returns its `created_card_ids`; a
retry after rollback finds nothing and may safely run again; the same key with
a different `request_hash` is a conflict rather than a second batch.
"""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels = None
depends_on = None

JSON_TYPE = JSONB().with_variant(sa.JSON(), "sqlite")

DISPOSITIONS = (
    "suggested",
    "not_suggested",
    "existing",
    "possible_overlap",
    "accepted",
    "skipped",
)
DUPLICATE_RESULTS = ("none", "exact", "possible")
ACCEPTANCE_STATUSES = ("processing", "committed", "failed")


def _quoted(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{v}'" for v in values)


def upgrade() -> None:
    op.create_table(
        "study_plan_card_proposals",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "plan_id",
            UUID(as_uuid=True),
            sa.ForeignKey("study_plans.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "source_plan_item_id",
            UUID(as_uuid=True),
            sa.ForeignKey("study_plan_items.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("topic", sa.Text(), nullable=False),
        sa.Column("category", sa.Text(), nullable=False, server_default="Unsorted"),
        # Persisted onto the card as `canonical_question` when accepted, so an
        # approved question is never regenerated on the first review.
        sa.Column("canonical_question", sa.Text(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False, server_default=""),
        # Five {question, passed, reason} objects, in gate order.
        sa.Column("gate_results", JSON_TYPE, nullable=False),
        sa.Column(
            "duplicate_check_result", sa.Text(), nullable=False, server_default="none"
        ),
        sa.Column(
            "duplicate_card_id",
            UUID(as_uuid=True),
            sa.ForeignKey("cards.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("disposition", sa.Text(), nullable=False, server_default="suggested"),
        # A cache of normalize_topic(topic). The authoritative check is re-run
        # inside the acceptance transaction against `cards` itself.
        sa.Column("normalized_topic", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            f"disposition IN ({_quoted(DISPOSITIONS)})",
            name="ck_study_plan_card_proposals_disposition",
        ),
        sa.CheckConstraint(
            f"duplicate_check_result IN ({_quoted(DUPLICATE_RESULTS)})",
            name="ck_study_plan_card_proposals_duplicate",
        ),
        sa.CheckConstraint("revision >= 1", name="ck_study_plan_card_proposals_revision"),
    )
    op.create_index(
        "ix_study_plan_card_proposals_item",
        "study_plan_card_proposals",
        ["source_plan_item_id"],
    )

    op.create_table(
        "study_plan_card_proposal_acceptances",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "proposal_id",
            UUID(as_uuid=True),
            sa.ForeignKey("study_plan_card_proposals.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("idempotency_key", sa.Text(), nullable=False),
        sa.Column("request_hash", sa.Text(), nullable=False),
        sa.Column("proposal_revision", sa.Integer(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default="processing"),
        sa.Column("created_card_ids", JSON_TYPE, nullable=False),
        sa.Column("committed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            f"status IN ({_quoted(ACCEPTANCE_STATUSES)})",
            name="ck_study_plan_acceptance_status",
        ),
        # `committed_at` is set in the same transaction as the cards. A committed
        # row without one, or a timestamp without the status, would make the
        # idempotent-replay lookup ambiguous.
        sa.CheckConstraint(
            "(status = 'committed') = (committed_at IS NOT NULL)",
            name="ck_study_plan_acceptance_committed_at",
        ),
    )
    op.create_index(
        "uq_study_plan_acceptance_key",
        "study_plan_card_proposal_acceptances",
        ["idempotency_key"],
        unique=True,
    )

    op.create_table(
        "study_plan_card_links",
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
        # Deleting a card removes the link. Deleting a plan removes the link and
        # leaves the card untouched — the cascade never runs the other way.
        sa.Column(
            "card_id",
            UUID(as_uuid=True),
            sa.ForeignKey("cards.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "acceptance_id",
            UUID(as_uuid=True),
            sa.ForeignKey(
                "study_plan_card_proposal_acceptances.id", ondelete="CASCADE"
            ),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_study_plan_card_links_plan", "study_plan_card_links", ["plan_id"])

    op.create_table(
        "study_plan_duplications",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "source_plan_id",
            UUID(as_uuid=True),
            sa.ForeignKey("study_plans.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "duplicated_plan_id",
            UUID(as_uuid=True),
            sa.ForeignKey("study_plans.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("study_plan_duplications")
    op.drop_index("ix_study_plan_card_links_plan", table_name="study_plan_card_links")
    op.drop_table("study_plan_card_links")
    op.drop_index(
        "uq_study_plan_acceptance_key",
        table_name="study_plan_card_proposal_acceptances",
    )
    op.drop_table("study_plan_card_proposal_acceptances")
    op.drop_index(
        "ix_study_plan_card_proposals_item", table_name="study_plan_card_proposals"
    )
    op.drop_table("study_plan_card_proposals")
