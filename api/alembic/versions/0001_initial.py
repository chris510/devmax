"""initial schema

Revision ID: 0001
Revises:
Create Date: 2026-07-24
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: str | None = None
branch_labels = None
depends_on = None

DEFAULT_WINDOWS = (
    '[{"label": "Morning", "from": "07:10", "to": "08:30", "on": true},'
    ' {"label": "Evening", "from": "21:00", "to": "22:30", "on": true}]'
)


def upgrade() -> None:
    op.create_table(
        "cards",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("topic", sa.Text(), nullable=False),
        sa.Column("category", sa.Text(), nullable=False),
        sa.Column("pattern", sa.Text(), nullable=True),
        sa.Column("source_company", sa.Text(), nullable=True),
        sa.Column("target_week", sa.Integer(), nullable=True),
        sa.Column("delivery_mode", sa.Text(), nullable=False),
        sa.Column("ease_factor", sa.Float(), nullable=False, server_default="2.5"),
        sa.Column("interval_days", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("repetitions", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("next_review_at", sa.Date(), nullable=False),
        sa.Column("last_score", sa.SmallInteger(), nullable=True),
        sa.Column("mastery_summary", sa.Text(), nullable=False, server_default=""),
        sa.Column("missed_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_pushed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "delivery_mode IN ('conversational', 'desk')", name="ck_cards_delivery_mode"
        ),
        sa.CheckConstraint("last_score IS NULL OR last_score BETWEEN 0 AND 5", name="ck_cards_score"),
    )
    op.create_index("ix_cards_next_review_at", "cards", ["next_review_at"])
    # The hot query — due conversational cards. Desk cards never enter the push loop.
    op.create_index(
        "ix_cards_due_conversational",
        "cards",
        ["next_review_at"],
        postgresql_where=sa.text("delivery_mode = 'conversational'"),
    )

    op.create_table(
        "sessions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "card_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("cards.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("question_asked", sa.Text(), nullable=False),
        sa.Column("follow_up_question", sa.Text(), nullable=True),
        sa.Column("answer_text", sa.Text(), nullable=False, server_default=""),
        sa.Column("follow_up_answer", sa.Text(), nullable=False, server_default=""),
        sa.Column("draft_text", sa.Text(), nullable=False, server_default=""),
        sa.Column("score", sa.SmallInteger(), nullable=True),
        sa.Column("feedback", sa.Text(), nullable=False, server_default=""),
        sa.Column("follow_up_used", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('open', 'awaiting_follow_up', 'complete', 'abandoned')",
            name="ck_sessions_status",
        ),
    )
    op.create_index(
        "ix_sessions_card_started", "sessions", ["card_id", sa.text("started_at DESC")]
    )

    op.create_table(
        "device_tokens",
        sa.Column("token", sa.Text(), primary_key=True),
        sa.Column("kind", sa.Text(), nullable=False, server_default="apns"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )

    op.create_table(
        "settings",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("reviews_per_day", sa.Integer(), nullable=False, server_default="2"),
        sa.Column("windows", postgresql.JSONB(), nullable=False),
        sa.Column(
            "timezone", sa.Text(), nullable=False, server_default="America/Los_Angeles"
        ),
        sa.CheckConstraint("id = 1", name="ck_settings_singleton"),
    )
    op.execute(
        "INSERT INTO settings (id, reviews_per_day, windows, timezone) "
        f"VALUES (1, 2, '{DEFAULT_WINDOWS}'::jsonb, 'America/Los_Angeles')"
    )


def downgrade() -> None:
    op.drop_table("settings")
    op.drop_table("device_tokens")
    op.drop_table("sessions")
    op.drop_table("cards")
