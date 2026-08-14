"""freeze scoring provider routes and retain privacy-safe call evidence

Revision ID: 0016
Revises: 0015
Create Date: 2026-08-13

Written by hand because autogenerate is deliberately disabled. This migration
adds metadata only: no score, transcript, card state, or SM-2 field is read or
rewritten. Historical sessions retain an empty route and therefore continue to
resolve to Anthropic.
"""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision: str = "0016"
down_revision: str | None = "0015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    empty_object = sa.text("'{}'::jsonb")
    op.add_column(
        "sessions",
        sa.Column(
            "scoring_route", JSONB(), nullable=False, server_default=empty_object
        ),
    )
    op.add_column(
        "llm_usage",
        sa.Column("details", JSONB(), nullable=False, server_default=empty_object),
    )


def downgrade() -> None:
    op.drop_column("llm_usage", "details")
    op.drop_column("sessions", "scoring_route")
