"""scored follow-up probes become rows

Revision ID: 0015
Revises: 0014
Create Date: 2026-08-13

Written by hand — `alembic revision --autogenerate` is disabled on purpose
(`target_metadata = None`); see docs/DEVIATIONS.md §9.

The reversal 0003 named the condition for. That migration kept the coached
re-attempt in scalar columns and argued that a turns table would model it as
"another follow-up"; docs/multi-turn-coaching-design.md §5.1 added the condition
under which the choice flips — "if the cap ever legitimately becomes N, the turns
table is the right move and this decision should be reversed on purpose, not
drifted past". The scored follow-up cap is becoming N, so its probes move to
rows, on purpose. The re-attempt and coaching columns stay scalar: they are
post-correction turns barred from SM-2, and this table cannot absorb them because
every row in it is by definition scored and pre-correction.

No `CHECK (idx <= 2)`, deliberately. The cap is one decision and it lives in one
place — `llm.MAX_SCORED_FOLLOW_UPS`, re-checked at the write site. A copy here
could only be changed by another migration, so the two would eventually disagree,
and the schema copy would win in the least useful way: a failed write on the turn
the code believed was allowed. `idx >= 1` is not that; it is the column's own
meaning (1-based order), which no code change can move.

`sessions.follow_up_question`, `follow_up_answer` and `follow_up_used` are kept
and frozen rather than dropped. Every historical row keeps its own evidence, the
downgrade below has somewhere to put a probe back, and `follow_up_used` stays
truthful — it goes on meaning "a scored probe was issued in this session".

The backfill writes one `idx = 1` row per session that already has a probe, with
`created_at = started_at`: the instant the probe was issued was never recorded,
and the session's own start is the only honest bound that still orders probes
correctly against every other session. A Python row loop rather than an
`INSERT ... SELECT` because the ids are generated here and the data is one user's
review history.

The downgrade copies `idx = 1` back into the scalars only where they are null — a
pre-upgrade session already holds its own copy, and overwriting it would let a
round trip rewrite history. **`idx = 2` rows are lost.** There is no second pair
of scalar columns to hold them, and inventing one would recreate the schema-level
cap this migration exists not to have. Downgrading past 0015 with second probes
on disk discards them; no score, axis, feedback or SM-2 field is touched either
way.
"""

import uuid

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

from alembic import op

revision: str = "0015"
down_revision: str | None = "0014"
branch_labels = None
depends_on = None

# Lightweight table clauses for the data moves below. Typed columns so the values
# round-trip as `uuid.UUID` and aware datetimes rather than whatever the driver
# hands back.
sessions = sa.table(
    "sessions",
    sa.column("id", UUID(as_uuid=True)),
    sa.column("follow_up_question", sa.Text()),
    sa.column("follow_up_answer", sa.Text()),
    sa.column("started_at", sa.DateTime(timezone=True)),
)
session_probes = sa.table(
    "session_probes",
    sa.column("id", UUID(as_uuid=True)),
    sa.column("session_id", UUID(as_uuid=True)),
    sa.column("idx", sa.SmallInteger()),
    sa.column("question", sa.Text()),
    sa.column("answer", sa.Text()),
    sa.column("created_at", sa.DateTime(timezone=True)),
)


def upgrade() -> None:
    op.create_table(
        "session_probes",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "session_id",
            UUID(as_uuid=True),
            sa.ForeignKey("sessions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        # 1-based order. How high it may go is a code constant, not a constraint.
        sa.Column("idx", sa.SmallInteger(), nullable=False),
        sa.Column("question", sa.Text(), nullable=False),
        # "" means this probe has been asked and not yet answered.
        sa.Column("answer", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("idx >= 1", name="ck_session_probes_idx"),
    )
    # Also the read path: probes for one session, in order.
    op.create_index(
        "uq_session_probes_session_idx",
        "session_probes",
        ["session_id", "idx"],
        unique=True,
    )

    bind = op.get_bind()
    legacy = bind.execute(
        sa.select(
            sessions.c.id,
            sessions.c.follow_up_question,
            sessions.c.follow_up_answer,
            sessions.c.started_at,
        ).where(sessions.c.follow_up_question.is_not(None))
    ).all()
    for row in legacy:
        bind.execute(
            session_probes.insert().values(
                id=uuid.uuid4(),
                session_id=row.id,
                idx=1,
                question=row.follow_up_question,
                answer=row.follow_up_answer or "",
                created_at=row.started_at,
            )
        )


def downgrade() -> None:
    bind = op.get_bind()
    first_probes = bind.execute(
        sa.select(
            session_probes.c.session_id,
            session_probes.c.question,
            session_probes.c.answer,
        ).where(session_probes.c.idx == 1)
    ).all()
    for row in first_probes:
        bind.execute(
            sessions.update()
            .where(sessions.c.id == row.session_id)
            .where(sessions.c.follow_up_question.is_(None))
            .values(follow_up_question=row.question, follow_up_answer=row.answer)
        )

    op.drop_index("uq_session_probes_session_idx", table_name="session_probes")
    op.drop_table("session_probes")
