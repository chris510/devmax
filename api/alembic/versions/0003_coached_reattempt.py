"""a coached re-attempt turn that never reaches the scheduler

Revision ID: 0003
Revises: 0002
Create Date: 2026-07-28

Written by hand — `alembic revision --autogenerate` is disabled on purpose
(`target_metadata = None`); see docs/DEVIATIONS.md §9.

Scalar columns rather than a `session_turns` table, deliberately. A turns table
would model this as "another follow-up"; it is not one. Turn 3 happens *after* the
correction has been stated, so it measures coached performance and is barred from
SM-2 — a different kind of turn, kept distinct in the schema so the cap cannot grow
by accident. See docs/multi-turn-coaching-design.md §5.1.

The prompt itself is deliberately not stored. It is a fixed preface plus the card's
own `question_asked`, nothing reads it back, and persisting it would put the same
string on both sides of the wire with only a comment holding them equal.

No backfill. Existing sessions get `reattempt_used = false` and null columns, which
is exactly what a session that never used one should look like.
"""

import sqlalchemy as sa

from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "sessions",
        sa.Column("reattempt_answer", sa.Text(), nullable=False, server_default=""),
    )
    op.add_column(
        "sessions", sa.Column("reattempt_mechanism_accuracy", sa.SmallInteger(), nullable=True)
    )
    op.add_column(
        "sessions",
        sa.Column("reattempt_used", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    # Mirrors the per-axis constraints 0002 added.
    op.create_check_constraint(
        "ck_sessions_reattempt_mechanism_accuracy",
        "sessions",
        "reattempt_mechanism_accuracy IS NULL OR reattempt_mechanism_accuracy BETWEEN 0 AND 5",
    )


def downgrade() -> None:
    op.drop_constraint("ck_sessions_reattempt_mechanism_accuracy", "sessions", type_="check")
    op.drop_column("sessions", "reattempt_used")
    op.drop_column("sessions", "reattempt_mechanism_accuracy")
    op.drop_column("sessions", "reattempt_answer")
