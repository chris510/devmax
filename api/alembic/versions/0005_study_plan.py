"""study plan core: plans, phases, weeks, items, dependencies, revisions, guide drafts

Revision ID: 0005
Revises: 0004
Create Date: 2026-07-29

Written by hand — `alembic revision --autogenerate` is disabled on purpose
(`target_metadata = None`); see docs/DEVIATIONS.md §9.

No backfill, and nothing here touches `cards` or `sessions`. Study Plan is
additive: an existing install upgrades to zero plans, which is a valid state —
Today shows `PLAN · ADD A STUDY GUIDE` until one is created.

`uq_study_plans_one_active` is the load-bearing constraint. "At most one active
plan" is enforced by a partial unique index rather than a read-then-write in the
service layer, so a concurrent activate is an IntegrityError instead of two
active plans. Both Postgres and SQLite honour partial unique indexes, which is
what lets the SQLite suite exercise the production rule.

Every timestamp is `timestamptz` — see docs/DEVIATIONS.md §6 for why a naive
column here fails every insert on Postgres while passing on SQLite.
"""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels = None
depends_on = None

# JSONB in production, JSON in the SQLite test schema. Mirrors models.JSON_TYPE.
JSON_TYPE = JSONB().with_variant(sa.JSON(), "sqlite")


def _quoted(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{v}'" for v in values)


PLAN_STATUSES = ("active", "paused", "completed", "archived")
PLAN_MODES = ("flexible", "fixed")
ITEM_TYPES = ("learn", "practice", "retrieve")
PRIORITIES = ("core", "optional", "recurring")
ITEM_STATUSES = ("pending", "complete", "deferred", "removed")
ORIGINS = ("imported", "generated", "manual")
ESTIMATE_SOURCES = ("imported", "generated", "user_edited")
CONFIDENCES = ("high", "medium", "needs_review")
DEP_KINDS = ("hard", "soft")
DEP_SOURCES = ("imported", "inferred", "user_added")
DRAFT_STATUSES = ("pending", "ready", "failed")


def upgrade() -> None:
    op.create_table(
        "study_plans",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("subject", sa.Text(), nullable=False),
        sa.Column("subject_slug", sa.Text(), nullable=False),
        # Verbatim. Item source offsets index into this exact string.
        sa.Column("guide_text", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default="active"),
        sa.Column("mode", sa.Text(), nullable=False, server_default="flexible"),
        sa.Column("deadline", sa.Date(), nullable=True),
        sa.Column("start_date", sa.Date(), nullable=False),
        sa.Column("default_weekly_capacity_minutes", sa.Integer(), nullable=False),
        sa.Column("current_week_index", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("forecast_end_plan_week", sa.Integer(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("paused_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            f"status IN ({_quoted(PLAN_STATUSES)})", name="ck_study_plans_status"
        ),
        sa.CheckConstraint(f"mode IN ({_quoted(PLAN_MODES)})", name="ck_study_plans_mode"),
        # A fixed plan without a deadline has nothing to validate against, and a
        # flexible plan with one would silently ignore it.
        sa.CheckConstraint(
            "(mode = 'fixed') = (deadline IS NOT NULL)", name="ck_study_plans_deadline_mode"
        ),
        sa.CheckConstraint(
            "default_weekly_capacity_minutes > 0", name="ck_study_plans_capacity_positive"
        ),
        sa.CheckConstraint("current_week_index >= 1", name="ck_study_plans_current_week"),
        sa.CheckConstraint("revision >= 1", name="ck_study_plans_revision"),
    )
    op.create_index(
        "uq_study_plans_one_active",
        "study_plans",
        ["status"],
        unique=True,
        postgresql_where=sa.text("status = 'active'"),
        sqlite_where=sa.text("status = 'active'"),
    )
    op.create_index("ix_study_plans_status", "study_plans", ["status"])

    op.create_table(
        "study_plan_phases",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "plan_id",
            UUID(as_uuid=True),
            sa.ForeignKey("study_plans.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("index", sa.Integer(), nullable=False),
        sa.Column("full_title", sa.Text(), nullable=False),
        # Display only — never reaches scheduling, dependency, or card logic.
        sa.Column("overview_title", sa.Text(), nullable=False, server_default=""),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.CheckConstraint('"index" >= 1', name="ck_study_plan_phases_index"),
    )
    op.create_index("ix_study_plan_phases_plan", "study_plan_phases", ["plan_id", "index"])

    op.create_table(
        "study_plan_weeks",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "plan_id",
            UUID(as_uuid=True),
            sa.ForeignKey("study_plans.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "phase_id",
            UUID(as_uuid=True),
            sa.ForeignKey("study_plan_phases.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("index", sa.Integer(), nullable=False),
        sa.Column("full_title", sa.Text(), nullable=False),
        sa.Column("overview_title", sa.Text(), nullable=False, server_default=""),
        # Null means the plan default applies. Storing the default here instead
        # would pin the week the next time the plan default moved.
        sa.Column("override_capacity_minutes", sa.Integer(), nullable=True),
        sa.Column("advanced_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint('"index" >= 1', name="ck_study_plan_weeks_index"),
        sa.CheckConstraint(
            "override_capacity_minutes IS NULL OR override_capacity_minutes > 0",
            name="ck_study_plan_weeks_override_positive",
        ),
    )
    op.create_index("ix_study_plan_weeks_plan", "study_plan_weeks", ["plan_id", "index"])

    op.create_table(
        "study_plan_items",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "plan_id",
            UUID(as_uuid=True),
            sa.ForeignKey("study_plans.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "phase_id",
            UUID(as_uuid=True),
            sa.ForeignKey("study_plan_phases.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "week_id",
            UUID(as_uuid=True),
            sa.ForeignKey("study_plan_weeks.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("guide_order", sa.Integer(), nullable=False),
        sa.Column("type", sa.Text(), nullable=False),
        sa.Column("priority", sa.Text(), nullable=False),
        sa.Column("full_title", sa.Text(), nullable=False),
        sa.Column("why_it_matters", sa.Text(), nullable=False, server_default=""),
        sa.Column("done_when", sa.Text(), nullable=False, server_default=""),
        sa.Column("estimate_minutes", sa.Integer(), nullable=False),
        sa.Column("estimate_source", sa.Text(), nullable=False, server_default="imported"),
        sa.Column("estimate_confidence", sa.Text(), nullable=False, server_default="high"),
        sa.Column("status", sa.Text(), nullable=False, server_default="pending"),
        sa.Column("origin", sa.Text(), nullable=False, server_default="imported"),
        sa.Column(
            "source_item_id",
            UUID(as_uuid=True),
            sa.ForeignKey("study_plan_items.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("source_start", sa.Integer(), nullable=True),
        sa.Column("source_end", sa.Integer(), nullable=True),
        sa.Column("source_excerpt", sa.Text(), nullable=False, server_default=""),
        sa.Column("parser_interpretation", sa.Text(), nullable=False, server_default=""),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reopened_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("notes", sa.Text(), nullable=False, server_default=""),
        # Reminder metadata, deliberately outside every schedule calculation.
        sa.Column("study_block_label", sa.Text(), nullable=False, server_default=""),
        sa.Column("study_block_weekday", sa.Integer(), nullable=True),
        sa.Column("study_block_minute_of_day", sa.Integer(), nullable=True),
        sa.Column(
            "study_block_reminder_on", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(f"type IN ({_quoted(ITEM_TYPES)})", name="ck_study_plan_items_type"),
        sa.CheckConstraint(
            f"priority IN ({_quoted(PRIORITIES)})", name="ck_study_plan_items_priority"
        ),
        sa.CheckConstraint(
            f"status IN ({_quoted(ITEM_STATUSES)})", name="ck_study_plan_items_status"
        ),
        sa.CheckConstraint(f"origin IN ({_quoted(ORIGINS)})", name="ck_study_plan_items_origin"),
        sa.CheckConstraint(
            f"estimate_source IN ({_quoted(ESTIMATE_SOURCES)})",
            name="ck_study_plan_items_estimate_source",
        ),
        sa.CheckConstraint(
            f"estimate_confidence IN ({_quoted(CONFIDENCES)})",
            name="ck_study_plan_items_estimate_confidence",
        ),
        # 30-minute increments, enforced here so no path can write 45 minutes and
        # make the displayed hour totals unreconcilable.
        sa.CheckConstraint(
            "estimate_minutes > 0 AND estimate_minutes % 30 = 0",
            name="ck_study_plan_items_estimate_increment",
        ),
        sa.CheckConstraint(
            "study_block_weekday IS NULL OR study_block_weekday BETWEEN 1 AND 7",
            name="ck_study_plan_items_weekday",
        ),
        sa.CheckConstraint(
            "study_block_minute_of_day IS NULL "
            "OR study_block_minute_of_day BETWEEN 0 AND 1439",
            name="ck_study_plan_items_minute_of_day",
        ),
    )
    op.create_index("ix_study_plan_items_week", "study_plan_items", ["week_id", "guide_order"])
    op.create_index("ix_study_plan_items_plan", "study_plan_items", ["plan_id"])

    op.create_table(
        "study_plan_item_dependencies",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "plan_id",
            UUID(as_uuid=True),
            sa.ForeignKey("study_plans.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "prerequisite_item_id",
            UUID(as_uuid=True),
            sa.ForeignKey("study_plan_items.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "dependent_item_id",
            UUID(as_uuid=True),
            sa.ForeignKey("study_plan_items.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("kind", sa.Text(), nullable=False, server_default="hard"),
        sa.Column("source", sa.Text(), nullable=False, server_default="imported"),
        sa.Column("confidence", sa.Text(), nullable=False, server_default="high"),
        sa.Column("rationale", sa.Text(), nullable=False, server_default=""),
        sa.Column("source_excerpt", sa.Text(), nullable=False, server_default=""),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            f"kind IN ({_quoted(DEP_KINDS)})", name="ck_study_plan_deps_kind"
        ),
        sa.CheckConstraint(
            f"source IN ({_quoted(DEP_SOURCES)})", name="ck_study_plan_deps_source"
        ),
        sa.CheckConstraint(
            f"confidence IN ({_quoted(CONFIDENCES)})", name="ck_study_plan_deps_confidence"
        ),
        sa.CheckConstraint(
            "prerequisite_item_id <> dependent_item_id", name="ck_study_plan_deps_not_self"
        ),
    )
    op.create_index("ix_study_plan_deps_plan", "study_plan_item_dependencies", ["plan_id"])

    op.create_table(
        "study_plan_revisions",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "plan_id",
            UUID(as_uuid=True),
            sa.ForeignKey("study_plans.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("base_plan_revision", sa.Integer(), nullable=False),
        sa.Column("before", JSON_TYPE, nullable=False),
        sa.Column("after", JSON_TYPE, nullable=False),
        sa.Column("reversible", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("summary", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_study_plan_revisions_plan",
        "study_plan_revisions",
        ["plan_id", sa.text("created_at DESC")],
    )

    op.create_table(
        "study_plan_guide_drafts",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("guide_text", sa.Text(), nullable=False),
        sa.Column("requested_weeks", sa.Integer(), nullable=False),
        sa.Column("weekly_capacity_minutes", sa.Integer(), nullable=False),
        sa.Column("mode", sa.Text(), nullable=False, server_default="flexible"),
        sa.Column("deadline", sa.Date(), nullable=True),
        sa.Column("start_date", sa.Date(), nullable=False),
        sa.Column("subject_hint", sa.Text(), nullable=False, server_default=""),
        sa.Column("title_hint", sa.Text(), nullable=False, server_default=""),
        sa.Column("status", sa.Text(), nullable=False, server_default="pending"),
        sa.Column("preview", JSON_TYPE, nullable=False),
        sa.Column("raw_response", JSON_TYPE, nullable=False),
        sa.Column("checks", JSON_TYPE, nullable=False),
        sa.Column("error", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            f"status IN ({_quoted(DRAFT_STATUSES)})", name="ck_study_plan_drafts_status"
        ),
        sa.CheckConstraint(f"mode IN ({_quoted(PLAN_MODES)})", name="ck_study_plan_drafts_mode"),
    )


def downgrade() -> None:
    op.drop_table("study_plan_guide_drafts")
    op.drop_index("ix_study_plan_revisions_plan", table_name="study_plan_revisions")
    op.drop_table("study_plan_revisions")
    op.drop_index("ix_study_plan_deps_plan", table_name="study_plan_item_dependencies")
    op.drop_table("study_plan_item_dependencies")
    op.drop_index("ix_study_plan_items_plan", table_name="study_plan_items")
    op.drop_index("ix_study_plan_items_week", table_name="study_plan_items")
    op.drop_table("study_plan_items")
    op.drop_index("ix_study_plan_weeks_plan", table_name="study_plan_weeks")
    op.drop_table("study_plan_weeks")
    op.drop_index("ix_study_plan_phases_plan", table_name="study_plan_phases")
    op.drop_table("study_plan_phases")
    op.drop_index("ix_study_plans_status", table_name="study_plans")
    op.drop_index("uq_study_plans_one_active", table_name="study_plans")
    op.drop_table("study_plans")
