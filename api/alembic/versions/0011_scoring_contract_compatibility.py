"""add scoring-contract compatibility metadata and qualitative turn storage

Revision ID: 0011
Revises: 0010
Create Date: 2026-08-11

Written by hand — `alembic revision --autogenerate` is disabled on purpose
(`target_metadata = None`); see docs/DEVIATIONS.md §9.

This is compatibility storage only. Existing numeric scores and every SM-2
field are left untouched. Existing scored rows are marked V1; no score is
recomputed or copied into a field with a new meaning.
"""

import sqlalchemy as sa

from alembic import op

revision: str = "0011"
down_revision: str | None = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "sessions",
        sa.Column(
            "scoring_contract_version",
            sa.SmallInteger(),
            nullable=False,
            server_default="1",
        ),
    )
    op.create_check_constraint(
        "ck_sessions_scoring_contract_version",
        "sessions",
        "scoring_contract_version IN (1, 2)",
    )

    op.add_column(
        "cards",
        sa.Column("last_score_contract_version", sa.SmallInteger(), nullable=True),
    )
    op.create_check_constraint(
        "ck_cards_last_score_contract_version",
        "cards",
        "last_score_contract_version IS NULL OR last_score_contract_version IN (1, 2)",
    )
    op.execute(
        "UPDATE cards SET last_score_contract_version = 1 "
        "WHERE last_score IS NOT NULL"
    )

    op.add_column("sessions", sa.Column("coaching_focus", sa.Text(), nullable=True))
    op.add_column("sessions", sa.Column("coaching_question", sa.Text(), nullable=True))
    op.add_column("sessions", sa.Column("coaching_answer", sa.Text(), nullable=True))
    op.add_column("sessions", sa.Column("coaching_feedback", sa.Text(), nullable=True))
    op.create_check_constraint(
        "ck_sessions_coaching_focus",
        "sessions",
        "coaching_focus IS NULL OR coaching_focus IN ('depth', 'boundaries')",
    )


def downgrade() -> None:
    op.drop_constraint("ck_sessions_coaching_focus", "sessions", type_="check")
    op.drop_column("sessions", "coaching_feedback")
    op.drop_column("sessions", "coaching_answer")
    op.drop_column("sessions", "coaching_question")
    op.drop_column("sessions", "coaching_focus")

    op.drop_constraint("ck_cards_last_score_contract_version", "cards", type_="check")
    op.drop_column("cards", "last_score_contract_version")

    op.drop_constraint("ck_sessions_scoring_contract_version", "sessions", type_="check")
    op.drop_column("sessions", "scoring_contract_version")
