"""accounts, rotating sessions, and per-user aggregate ownership

Revision ID: 0008
Revises: 0007
Create Date: 2026-08-07

Written by hand. The existing installation is backfilled to one stable founder
user before any ownership column becomes NOT NULL. No card, session, score,
mastery summary, Study Plan progress, or SM-2 value is rewritten.
"""

import uuid
from datetime import UTC, datetime

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

from alembic import op

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels = None
depends_on = None

FOUNDER_USER_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")


def _add_owner(table: str, fk_name: str) -> None:
    op.add_column(table, sa.Column("user_id", UUID(as_uuid=True), nullable=True))
    op.execute(
        sa.text(f"UPDATE {table} SET user_id = :founder").bindparams(
            sa.bindparam("founder", FOUNDER_USER_ID, type_=UUID(as_uuid=True))
        )
    )
    op.alter_column(table, "user_id", nullable=False)
    op.create_foreign_key(
        fk_name, table, "users", ["user_id"], ["id"], ondelete="CASCADE"
    )


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("status", sa.Text(), nullable=False, server_default="active"),
        sa.Column("is_founder", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("status IN ('active', 'deleting')", name="ck_users_status"),
    )
    users = sa.table(
        "users",
        sa.column("id", UUID(as_uuid=True)),
        sa.column("status", sa.Text()),
        sa.column("is_founder", sa.Boolean()),
        sa.column("created_at", sa.DateTime(timezone=True)),
        sa.column("updated_at", sa.DateTime(timezone=True)),
    )
    now = datetime.now(UTC)
    op.bulk_insert(
        users,
        [
            {
                "id": FOUNDER_USER_ID,
                "status": "active",
                "is_founder": True,
                "created_at": now,
                "updated_at": now,
            }
        ],
    )

    op.create_table(
        "apple_identities",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "user_id",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("subject", sa.Text(), nullable=False),
        sa.Column("email", sa.Text(), nullable=True),
        sa.Column("display_name", sa.Text(), nullable=True),
        sa.Column("apple_refresh_token", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "uq_apple_identities_subject", "apple_identities", ["subject"], unique=True
    )
    op.create_index(
        "uq_apple_identities_user", "apple_identities", ["user_id"], unique=True
    )

    op.create_table(
        "auth_sessions",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "user_id",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("family_id", UUID(as_uuid=True), nullable=False),
        sa.Column("rotated_from_id", UUID(as_uuid=True), nullable=True),
        sa.Column("access_token_hash", sa.Text(), nullable=False),
        sa.Column("refresh_token_hash", sa.Text(), nullable=False),
        sa.Column("access_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("refresh_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "uq_auth_sessions_access_hash", "auth_sessions", ["access_token_hash"], unique=True
    )
    op.create_index(
        "uq_auth_sessions_refresh_hash", "auth_sessions", ["refresh_token_hash"], unique=True
    )
    op.create_index("ix_auth_sessions_user", "auth_sessions", ["user_id"])
    op.create_index("ix_auth_sessions_family", "auth_sessions", ["family_id"])

    op.create_table(
        "auth_nonces",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("nonce_hash", sa.Text(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("uq_auth_nonces_hash", "auth_nonces", ["nonce_hash"], unique=True)

    _add_owner("cards", "fk_cards_user_id_users")
    _add_owner("device_tokens", "fk_device_tokens_user_id_users")
    _add_owner("settings", "fk_settings_user_id_users")
    _add_owner("study_plans", "fk_study_plans_user_id_users")
    _add_owner("study_plan_guide_drafts", "fk_study_plan_guide_drafts_user_id_users")

    # Migration 0001 inserted the singleton settings row with an explicit id,
    # which does not advance Postgres's sequence. The first public account would
    # otherwise try id=1 again and fail after Apple sign-in had succeeded.
    if op.get_bind().dialect.name == "postgresql":
        op.execute(
            "SELECT setval(pg_get_serial_sequence('settings', 'id'), "
            "COALESCE((SELECT MAX(id) FROM settings), 1), true)"
        )

    op.create_index("ix_cards_user_next_review", "cards", ["user_id", "next_review_at"])
    op.create_index("ix_device_tokens_user", "device_tokens", ["user_id"])
    op.drop_constraint("ck_settings_singleton", "settings", type_="check")
    op.create_index("uq_settings_user", "settings", ["user_id"], unique=True)
    op.create_index(
        "ix_study_plan_guide_drafts_user", "study_plan_guide_drafts", ["user_id"]
    )

    op.drop_index("uq_study_plans_one_active", table_name="study_plans")
    op.drop_index("ix_study_plans_status", table_name="study_plans")
    op.create_index(
        "uq_study_plans_one_active",
        "study_plans",
        ["user_id", "status"],
        unique=True,
        postgresql_where=sa.text("status = 'active'"),
        sqlite_where=sa.text("status = 'active'"),
    )
    op.create_index("ix_study_plans_status", "study_plans", ["user_id", "status"])


def downgrade() -> None:
    op.drop_index("ix_study_plans_status", table_name="study_plans")
    op.drop_index("uq_study_plans_one_active", table_name="study_plans")
    op.create_index(
        "uq_study_plans_one_active",
        "study_plans",
        ["status"],
        unique=True,
        postgresql_where=sa.text("status = 'active'"),
        sqlite_where=sa.text("status = 'active'"),
    )
    op.create_index("ix_study_plans_status", "study_plans", ["status"])

    op.drop_index("ix_study_plan_guide_drafts_user", table_name="study_plan_guide_drafts")
    op.drop_index("uq_settings_user", table_name="settings")
    op.create_check_constraint("ck_settings_singleton", "settings", "id = 1")
    op.drop_index("ix_device_tokens_user", table_name="device_tokens")
    op.drop_index("ix_cards_user_next_review", table_name="cards")

    for table, fk_name in (
        ("study_plan_guide_drafts", "fk_study_plan_guide_drafts_user_id_users"),
        ("study_plans", "fk_study_plans_user_id_users"),
        ("settings", "fk_settings_user_id_users"),
        ("device_tokens", "fk_device_tokens_user_id_users"),
        ("cards", "fk_cards_user_id_users"),
    ):
        op.drop_constraint(fk_name, table, type_="foreignkey")
        op.drop_column(table, "user_id")

    op.drop_index("uq_auth_nonces_hash", table_name="auth_nonces")
    op.drop_table("auth_nonces")
    op.drop_index("ix_auth_sessions_family", table_name="auth_sessions")
    op.drop_index("ix_auth_sessions_user", table_name="auth_sessions")
    op.drop_index("uq_auth_sessions_refresh_hash", table_name="auth_sessions")
    op.drop_index("uq_auth_sessions_access_hash", table_name="auth_sessions")
    op.drop_table("auth_sessions")
    op.drop_index("uq_apple_identities_user", table_name="apple_identities")
    op.drop_index("uq_apple_identities_subject", table_name="apple_identities")
    op.drop_table("apple_identities")
    op.drop_table("users")
