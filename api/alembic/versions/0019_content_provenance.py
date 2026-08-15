"""classify lesson content provenance before confirmation

Revision ID: 0019
Revises: 0018
Create Date: 2026-08-14

The classification describes the pasted content itself. It intentionally does
not replace the source genre (``kind``) or attribution URL (``source_url``).
Existing material stays explicitly unclassified instead of being guessed.
"""

import sqlalchemy as sa

from alembic import op

revision: str = "0019"
down_revision: str | None = "0018"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "material_sources",
        sa.Column(
            "content_provenance",
            sa.Text(),
            nullable=False,
            server_default="legacy_unspecified",
        ),
    )
    op.create_check_constraint(
        "ck_material_sources_content_provenance",
        "material_sources",
        "content_provenance IN ("
        "'legacy_unspecified',"
        "'exact_source_excerpt',"
        "'learner_notes',"
        "'coached_correction',"
        "'ai_derived_summary'"
        ")",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_material_sources_content_provenance",
        "material_sources",
        type_="check",
    )
    op.drop_column("material_sources", "content_provenance")
