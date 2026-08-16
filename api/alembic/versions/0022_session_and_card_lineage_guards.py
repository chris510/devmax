"""enforce one live session and one-to-one card lineage

Revision ID: 0022
Revises: 0021
Create Date: 2026-08-15

The application takes the card row lock before starting or maintaining a card.
These indexes are database backstops: a future code path cannot create two live
answers for one card or fork the scalar replacement lineage.

Older builds had no abandon endpoint and no live-session uniqueness constraint.
If a historical race left several live rows, keep the newest resumable and mark
the inaccessible older rows abandoned before creating the index. No answer,
score, or scheduling field is rewritten.
"""

import sqlalchemy as sa

from alembic import op

revision: str = "0022"
down_revision: str | None = "0021"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        WITH ranked AS (
            SELECT id,
                   row_number() OVER (
                       PARTITION BY card_id
                       ORDER BY started_at DESC, id DESC
                   ) AS live_rank
            FROM sessions
            WHERE status IN ('open', 'awaiting_follow_up')
        )
        UPDATE sessions AS session
        SET status = 'abandoned',
            ended_at = COALESCE(session.ended_at, now())
        FROM ranked
        WHERE session.id = ranked.id
          AND ranked.live_rank > 1
        """
    )
    op.create_index(
        "uq_sessions_live_card",
        "sessions",
        ["card_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('open', 'awaiting_follow_up')"),
    )
    op.create_index(
        "uq_cards_replaces_card",
        "cards",
        ["replaces_card_id"],
        unique=True,
        postgresql_where=sa.text("replaces_card_id IS NOT NULL"),
    )
    op.create_index(
        "uq_cards_replaced_by_card",
        "cards",
        ["replaced_by_card_id"],
        unique=True,
        postgresql_where=sa.text("replaced_by_card_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_cards_replaced_by_card", table_name="cards")
    op.drop_index("uq_cards_replaces_card", table_name="cards")
    op.drop_index("uq_sessions_live_card", table_name="sessions")
