"""scope Study Plan card-acceptance idempotency keys to a plan

Revision ID: 0023
Revises: 0022
Create Date: 2026-08-15

An acceptance already belongs to a plan through its proposal, but the original
unique index made the client-supplied key global across every plan. Persisting
the plan directly makes the lookup and database uniqueness boundary identical:
``(plan_id, idempotency_key)``.

The backfill follows the existing proposal foreign key and does not modify any
card, link, plan progress, score, session, mastery, or SM-2 state.
"""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

from alembic import op

revision: str = "0023"
down_revision: str | None = "0022"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "study_plan_card_proposal_acceptances",
        sa.Column("plan_id", UUID(as_uuid=True), nullable=True),
    )
    op.execute(
        """
        UPDATE study_plan_card_proposal_acceptances AS acceptance
        SET plan_id = (
            SELECT proposal.plan_id
            FROM study_plan_card_proposals AS proposal
            WHERE proposal.id = acceptance.proposal_id
        )
        """
    )
    op.create_foreign_key(
        "fk_study_plan_acceptances_plan_id_study_plans",
        "study_plan_card_proposal_acceptances",
        "study_plans",
        ["plan_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.alter_column(
        "study_plan_card_proposal_acceptances",
        "plan_id",
        nullable=False,
    )
    op.drop_index(
        "uq_study_plan_acceptance_key",
        table_name="study_plan_card_proposal_acceptances",
    )
    op.create_index(
        "uq_study_plan_acceptance_plan_key",
        "study_plan_card_proposal_acceptances",
        ["plan_id", "idempotency_key"],
        unique=True,
    )


def downgrade() -> None:
    # If callers have reused a key across plans, restoring global uniqueness
    # correctly fails rather than deleting or rewriting an acceptance record.
    op.drop_index(
        "uq_study_plan_acceptance_plan_key",
        table_name="study_plan_card_proposal_acceptances",
    )
    op.create_index(
        "uq_study_plan_acceptance_key",
        "study_plan_card_proposal_acceptances",
        ["idempotency_key"],
        unique=True,
    )
    op.drop_constraint(
        "fk_study_plan_acceptances_plan_id_study_plans",
        "study_plan_card_proposal_acceptances",
        type_="foreignkey",
    )
    op.drop_column("study_plan_card_proposal_acceptances", "plan_id")
