"""decompose the score into three axes; stable canonical question per card

Revision ID: 0002
Revises: 0001
Create Date: 2026-07-27

Written by hand — `alembic revision --autogenerate` is disabled on purpose
(`target_metadata = None`); see docs/DEVIATIONS.md §9.

No backfill. Old sessions keep their blended `score` for history display and
leave the three axis columns null; `derive_composite` only ever runs on new
rows. Cards likewise start with a null `canonical_question` and generate one on
their next session.
"""

import sqlalchemy as sa

from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels = None
depends_on = None

# Mirrors ck_cards_score / the 0-5 enum in llm.SCORE_SCHEMA.
AXES = ("mechanism_accuracy", "trade_off_awareness", "failure_mode_awareness")


def upgrade() -> None:
    op.add_column("cards", sa.Column("canonical_question", sa.Text(), nullable=True))
    op.add_column(
        "cards", sa.Column("last_reviewed_at", sa.DateTime(timezone=True), nullable=True)
    )
    for axis in AXES:
        op.add_column("cards", sa.Column(f"last_{axis}", sa.SmallInteger(), nullable=True))
        op.create_check_constraint(
            f"ck_cards_{axis}",
            "cards",
            f"last_{axis} IS NULL OR last_{axis} BETWEEN 0 AND 5",
        )

    for axis in AXES:
        op.add_column("sessions", sa.Column(axis, sa.SmallInteger(), nullable=True))
        op.create_check_constraint(
            f"ck_sessions_{axis}",
            "sessions",
            f"{axis} IS NULL OR {axis} BETWEEN 0 AND 5",
        )
    op.add_column(
        "sessions",
        sa.Column("practice", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column("sessions", "practice")
    for axis in AXES:
        op.drop_constraint(f"ck_sessions_{axis}", "sessions", type_="check")
        op.drop_column("sessions", axis)

    for axis in AXES:
        op.drop_constraint(f"ck_cards_{axis}", "cards", type_="check")
        op.drop_column("cards", f"last_{axis}")
    op.drop_column("cards", "last_reviewed_at")
    op.drop_column("cards", "canonical_question")
