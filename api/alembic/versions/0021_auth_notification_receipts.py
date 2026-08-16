"""make Apple notification ordering durable and auth cleanup indexable

Revision ID: 0021
Revises: 0020
Create Date: 2026-08-15

Apple's notification event timestamp is not an idempotency key: distinct
events can share the same second, and an email event must not suppress a
delayed revocation. The signed JWT ``jti`` is persisted instead.

``last_apple_authorized_at`` intentionally receives no historical backfill.
The old ``last_apple_event_at`` mixed authorizations with email and security
events, so treating it as proven authorization could preserve the exact
revocation-suppression bug this migration fixes. Existing accounts fail closed
until their next successful Apple authorization establishes the new boundary.
"""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

from alembic import op

revision: str = "0021"
down_revision: str | None = "0020"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "apple_identities",
        sa.Column("last_apple_authorized_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_table(
        "apple_notification_receipts",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "identity_id",
            UUID(as_uuid=True),
            sa.ForeignKey("apple_identities.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("jti", sa.Text(), nullable=False),
        sa.Column("event_type", sa.Text(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("applied", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "event_type IN ("
            "'email-disabled',"
            "'email-enabled',"
            "'consent-revoked',"
            "'account-deleted'"
            ")",
            name="ck_apple_notification_receipts_event_type",
        ),
    )
    op.create_index(
        "uq_apple_notification_receipts_jti",
        "apple_notification_receipts",
        ["jti"],
        unique=True,
    )
    op.create_index(
        "ix_apple_notification_receipts_identity_created",
        "apple_notification_receipts",
        ["identity_id", "created_at"],
    )
    op.create_index(
        "ix_apple_notification_receipts_created",
        "apple_notification_receipts",
        ["created_at"],
    )
    op.create_index("ix_auth_nonces_expires", "auth_nonces", ["expires_at"])
    op.create_index("ix_auth_nonces_used", "auth_nonces", ["used_at"])
    op.create_index(
        "ix_auth_sessions_refresh_expires",
        "auth_sessions",
        ["refresh_expires_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_auth_sessions_refresh_expires", table_name="auth_sessions")
    op.drop_index("ix_auth_nonces_used", table_name="auth_nonces")
    op.drop_index("ix_auth_nonces_expires", table_name="auth_nonces")
    op.drop_index(
        "ix_apple_notification_receipts_created",
        table_name="apple_notification_receipts",
    )
    op.drop_index(
        "ix_apple_notification_receipts_identity_created",
        table_name="apple_notification_receipts",
    )
    op.drop_index(
        "uq_apple_notification_receipts_jti",
        table_name="apple_notification_receipts",
    )
    op.drop_table("apple_notification_receipts")
    op.drop_column("apple_identities", "last_apple_authorized_at")
