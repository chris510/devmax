"""versioned third-party AI consent

Revision ID: 0014
Revises: 0013
Create Date: 2026-08-12

Written by hand because autogenerate is deliberately disabled.
"""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

from alembic import op

revision: str = "0014"
down_revision: str | None = "0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "apple_identities",
        sa.Column("authorization_revoked_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "apple_identities",
        sa.Column("last_apple_event_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "users",
        sa.Column("ai_consent_status", sa.Text(), nullable=False, server_default="pending"),
    )
    op.add_column(
        "users",
        sa.Column("ai_consent_version", sa.Text(), nullable=False, server_default=""),
    )
    op.add_column(
        "users", sa.Column("ai_consent_updated_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "users", sa.Column("ai_consent_granted_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.create_check_constraint(
        "ck_users_ai_consent_status",
        "users",
        "ai_consent_status IN ('pending', 'granted', 'declined', 'withdrawn')",
    )
    op.create_table(
        "ai_consent_events",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "user_id",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("policy_version", sa.Text(), nullable=False),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "action IN ('grant', 'decline', 'withdraw')",
            name="ck_ai_consent_events_action",
        ),
    )
    op.create_index(
        "ix_ai_consent_events_user_created",
        "ai_consent_events",
        ["user_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_ai_consent_events_user_created", table_name="ai_consent_events")
    op.drop_table("ai_consent_events")
    op.drop_constraint("ck_users_ai_consent_status", "users", type_="check")
    op.drop_column("users", "ai_consent_granted_at")
    op.drop_column("users", "ai_consent_updated_at")
    op.drop_column("users", "ai_consent_version")
    op.drop_column("users", "ai_consent_status")
    op.drop_column("apple_identities", "last_apple_event_at")
    op.drop_column("apple_identities", "authorization_revoked_at")
