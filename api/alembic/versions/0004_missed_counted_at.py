"""record which push was counted missed, instead of erasing it

Revision ID: 0004
Revises: 0003
Create Date: 2026-07-29

Written by hand — `alembic revision --autogenerate` is disabled on purpose
(`target_metadata = None`); see docs/DEVIATIONS.md §9.

`check-missed` used to clear `cards.last_pushed_at` after counting a push missed,
so the same push would not be counted twice. But `last_pushed_at` is also the only
evidence `trigger-review` has that a push already went out today: clearing it
silently gave the day's budget back, and `reviews_per_day` became approximate.

That was tolerable while the cron fired twice a day at fixed times. It is not once
the workflow polls every thirty minutes and the notification windows in `settings`
decide when a push goes out, because the same field now also answers "have we
already pushed inside *this* window". A nulled `last_pushed_at` would re-open a
window that had already been satisfied.

So the erasure moves to its own column. `missed_counted_at` holds the value of
`last_pushed_at` that has already been counted — a push instant, not a counting
instant — so `missed_counted_at < last_pushed_at` reads exactly as "this push has
not been counted yet" and re-running check-missed is a no-op. `last_pushed_at`
itself becomes durable.

No backfill. Existing rows get NULL, which reads as "no push has been counted
missed on this card" — correct for every row, including the ones whose
`last_pushed_at` the old code already cleared.
"""

import sqlalchemy as sa

from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "cards",
        sa.Column("missed_counted_at", sa.DateTime(timezone=True), nullable=True),
    )
    # The semantics above, made structural rather than left to the one writer that
    # currently maintains them. A stamp ahead of `last_pushed_at` would silently
    # suppress missed-counting on that card forever, and `missed_count` is the
    # product's only compliance signal — the kind of invariant the other ten CHECK
    # constraints in this schema exist to protect.
    op.create_check_constraint(
        "ck_cards_missed_counted_at",
        "cards",
        "missed_counted_at IS NULL OR "
        "(last_pushed_at IS NOT NULL AND missed_counted_at <= last_pushed_at)",
    )


def downgrade() -> None:
    op.drop_constraint("ck_cards_missed_counted_at", "cards", type_="check")
    op.drop_column("cards", "missed_counted_at")
