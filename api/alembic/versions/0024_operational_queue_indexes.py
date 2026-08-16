"""index global notification and durable import sweeps

Revision ID: 0024
Revises: 0023
Create Date: 2026-08-15

These are read-path-only changes. They do not backfill or mutate product data.
"""

import sqlalchemy as sa

from alembic import op

revision: str = "0024"
down_revision: str | None = "0023"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        "ix_cards_user_last_pushed",
        "cards",
        ["user_id", "last_pushed_at"],
    )
    op.create_index(
        "ix_cards_active_last_pushed",
        "cards",
        ["last_pushed_at"],
        postgresql_where=sa.text(
            "lifecycle_status = 'active' AND last_pushed_at IS NOT NULL"
        ),
        sqlite_where=sa.text(
            "lifecycle_status = 'active' AND last_pushed_at IS NOT NULL"
        ),
    )
    op.create_index(
        "ix_material_sources_recovery",
        "material_sources",
        ["status", "processing_heartbeat_at"],
        postgresql_where=sa.text("status IN ('pending', 'processing')"),
        sqlite_where=sa.text("status IN ('pending', 'processing')"),
    )


def downgrade() -> None:
    op.drop_index("ix_material_sources_recovery", table_name="material_sources")
    op.drop_index("ix_cards_active_last_pushed", table_name="cards")
    op.drop_index("ix_cards_user_last_pushed", table_name="cards")
