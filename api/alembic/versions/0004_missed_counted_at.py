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


def downgrade() -> None:
    op.drop_column("cards", "missed_counted_at")
