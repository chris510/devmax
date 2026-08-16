"""add the adaptive-study pilot's unscored storage boundary

Revision ID: 0020
Revises: 0019
Create Date: 2026-08-15

Written by hand because SQLModel metadata is not the migration source of truth.
Formation and transfer live outside ``sessions`` so this revision cannot write a
score, mastery value, or SM-2 field. Existing material rows receive only nullable
funnel/exposure metadata.
"""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

from alembic import op

revision: str = "0020"
down_revision: str | None = "0019"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "material_sources",
        sa.Column("proposals_ready_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "material_sources",
        sa.Column("review_opened_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "material_sources",
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_check_constraint(
        "ck_material_sources_proposals_ready_time",
        "material_sources",
        "proposals_ready_at IS NULL OR proposals_ready_at >= created_at",
    )
    op.create_check_constraint(
        "ck_material_sources_review_opened_time",
        "material_sources",
        "review_opened_at IS NULL OR proposals_ready_at IS NULL "
        "OR review_opened_at >= proposals_ready_at",
    )
    op.create_check_constraint(
        "ck_material_sources_confirmed_time",
        "material_sources",
        "confirmed_at IS NULL OR review_opened_at IS NULL "
        "OR confirmed_at >= review_opened_at",
    )

    op.add_column(
        "material_topic_proposals",
        sa.Column(
            "last_learning_exposure_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )
    op.add_column(
        "material_topic_proposals",
        sa.Column("recall_not_before_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_check_constraint(
        "ck_material_topic_proposals_exposure_pair",
        "material_topic_proposals",
        "(last_learning_exposure_at IS NULL) = (recall_not_before_at IS NULL)",
    )
    op.create_check_constraint(
        "ck_material_topic_proposals_recall_boundary",
        "material_topic_proposals",
        "recall_not_before_at IS NULL "
        "OR recall_not_before_at >= last_learning_exposure_at",
    )

    op.create_table(
        "lesson_checks",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "user_id",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "proposal_id",
            UUID(as_uuid=True),
            sa.ForeignKey("material_topic_proposals.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "card_id",
            UUID(as_uuid=True),
            sa.ForeignKey("cards.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("condition", sa.Text(), nullable=True),
        sa.Column("prompt_level", sa.Text(), nullable=False),
        sa.Column("prompt_version", sa.Text(), nullable=False),
        sa.Column(
            "provider_route",
            JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("source_candidate_id", sa.Text(), nullable=True),
        sa.Column("prompt_text_snapshot", sa.Text(), nullable=False),
        sa.Column("prompt_rubric_version", sa.Text(), nullable=False, server_default=""),
        sa.Column("prompt_reviewer_id", sa.Text(), nullable=True),
        sa.Column("prompt_approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.Text(), nullable=False, server_default="open"),
        sa.Column("draft_text", sa.Text(), nullable=False, server_default=""),
        sa.Column("answer_text", sa.Text(), nullable=False, server_default=""),
        sa.Column("qualitative_outcome", sa.Text(), nullable=False, server_default=""),
        sa.Column("feedback", sa.Text(), nullable=False, server_default=""),
        sa.Column("exposed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("recall_not_before_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "kind IN ('formation','transfer')",
            name="ck_lesson_checks_kind",
        ),
        sa.CheckConstraint(
            "condition IS NULL OR condition IN ('attempt_first','restudy')",
            name="ck_lesson_checks_condition",
        ),
        sa.CheckConstraint(
            "prompt_level IN ('canonical','application','failure_tradeoff')",
            name="ck_lesson_checks_prompt_level",
        ),
        sa.CheckConstraint(
            "(kind = 'formation' AND prompt_level = 'canonical') "
            "OR (kind = 'transfer' "
            "AND prompt_level IN ('application','failure_tradeoff'))",
            name="ck_lesson_checks_kind_prompt",
        ),
        sa.CheckConstraint(
            "status IN ('open','submitted','exposed')",
            name="ck_lesson_checks_status",
        ),
        sa.CheckConstraint(
            "qualitative_outcome IN ("
            "'', 'accurate_account', 'missing_mechanism', 'misconception', "
            "'missing_boundary', 'insufficient_evidence'"
            ")",
            name="ck_lesson_checks_qualitative_outcome",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(provider_route) = 'object'",
            name="ck_lesson_checks_provider_route",
        ),
        sa.CheckConstraint(
            "(status = 'exposed' AND exposed_at IS NOT NULL "
            "AND recall_not_before_at IS NOT NULL) "
            "OR (status <> 'exposed' AND exposed_at IS NULL "
            "AND recall_not_before_at IS NULL)",
            name="ck_lesson_checks_exposure_state",
        ),
        sa.CheckConstraint(
            "recall_not_before_at IS NULL OR recall_not_before_at >= exposed_at",
            name="ck_lesson_checks_recall_boundary",
        ),
        sa.CheckConstraint(
            "submitted_at IS NULL OR submitted_at >= started_at",
            name="ck_lesson_checks_submitted_time",
        ),
        sa.CheckConstraint(
            "exposed_at IS NULL OR exposed_at >= started_at",
            name="ck_lesson_checks_exposed_time",
        ),
    )
    op.create_index(
        "uq_lesson_checks_proposal_kind",
        "lesson_checks",
        ["proposal_id", "kind"],
        unique=True,
    )
    op.create_index(
        "ix_lesson_checks_user_status",
        "lesson_checks",
        ["user_id", "status"],
    )
    op.create_index(
        "ix_lesson_checks_user_available",
        "lesson_checks",
        ["user_id", "kind", "available_at"],
    )

    op.create_table(
        "lesson_proposal_audits",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "source_id",
            UUID(as_uuid=True),
            sa.ForeignKey("material_sources.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "proposal_id",
            UUID(as_uuid=True),
            sa.ForeignKey("material_topic_proposals.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "extraction_route",
            JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("extraction_prompt_version", sa.Text(), nullable=False),
        sa.Column("grounding_gate_version", sa.Text(), nullable=False),
        sa.Column(
            "original_proposal_pack",
            JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "original_grounding_findings",
            JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("reviewer_id", sa.Text(), nullable=False, server_default=""),
        sa.Column(
            "reviewer_decision",
            sa.Text(),
            nullable=False,
            server_default="pending",
        ),
        sa.Column(
            "reviewer_correction",
            JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "reviewer_decision IN ('pending','approved','corrected','blocked')",
            name="ck_lesson_proposal_audits_decision",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(extraction_route) = 'object'",
            name="ck_lesson_proposal_audits_extraction_route",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(original_proposal_pack) = 'object'",
            name="ck_lesson_proposal_audits_original_pack",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(original_grounding_findings) = 'array'",
            name="ck_lesson_proposal_audits_grounding_findings",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(reviewer_correction) = 'object'",
            name="ck_lesson_proposal_audits_correction",
        ),
    )
    op.create_index(
        "uq_lesson_proposal_audits_proposal",
        "lesson_proposal_audits",
        ["proposal_id"],
        unique=True,
    )
    op.create_index(
        "ix_lesson_proposal_audits_source",
        "lesson_proposal_audits",
        ["source_id"],
    )

    op.create_table(
        "study_pilot_enrollments",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "user_id",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("cohort", sa.Text(), nullable=False),
        sa.Column("consent_version", sa.Text(), nullable=False),
        sa.Column("consented_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("withdrawn_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("randomization_seed", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("cohort <> ''", name="ck_study_pilot_enrollments_cohort"),
        sa.CheckConstraint(
            "consent_version <> ''",
            name="ck_study_pilot_enrollments_consent_version",
        ),
        sa.CheckConstraint(
            "randomization_seed <> ''",
            name="ck_study_pilot_enrollments_randomization_seed",
        ),
        sa.CheckConstraint(
            "withdrawn_at IS NULL OR withdrawn_at >= consented_at",
            name="ck_study_pilot_enrollments_withdrawn_time",
        ),
    )
    op.create_index(
        "uq_study_pilot_enrollments_user_cohort",
        "study_pilot_enrollments",
        ["user_id", "cohort"],
        unique=True,
    )
    op.create_index(
        "uq_study_pilot_enrollments_active_user",
        "study_pilot_enrollments",
        ["user_id"],
        unique=True,
        postgresql_where=sa.text("withdrawn_at IS NULL"),
    )
    op.create_index(
        "uq_study_pilot_enrollments_randomization_seed",
        "study_pilot_enrollments",
        ["randomization_seed"],
        unique=True,
    )

    op.create_table(
        "study_pilot_assignments",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "enrollment_id",
            UUID(as_uuid=True),
            sa.ForeignKey("study_pilot_enrollments.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("source_lineage_id", UUID(as_uuid=True), nullable=False),
        sa.Column(
            "source_id",
            UUID(as_uuid=True),
            sa.ForeignKey("material_sources.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("pair_index", sa.SmallInteger(), nullable=False),
        sa.Column("sequence_index", sa.SmallInteger(), nullable=False),
        sa.Column("condition", sa.Text(), nullable=False),
        sa.Column("intended_target", sa.Text(), nullable=False),
        sa.Column(
            "target_proposal_id",
            UUID(as_uuid=True),
            sa.ForeignKey("material_topic_proposals.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "version_snapshot",
            JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("assigned_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("bound_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "pair_index BETWEEN 1 AND 3",
            name="ck_study_pilot_assignments_pair_index",
        ),
        sa.CheckConstraint(
            "sequence_index BETWEEN 1 AND 6",
            name="ck_study_pilot_assignments_sequence_index",
        ),
        sa.CheckConstraint(
            "condition IN ('attempt_first','restudy')",
            name="ck_study_pilot_assignments_condition",
        ),
        sa.CheckConstraint(
            "intended_target IN ('position:1','position:2','position:3')",
            name="ck_study_pilot_assignments_intended_target",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(version_snapshot) = 'object'",
            name="ck_study_pilot_assignments_version_snapshot",
        ),
        sa.CheckConstraint(
            "bound_at IS NULL OR bound_at >= assigned_at",
            name="ck_study_pilot_assignments_bound_time",
        ),
    )
    op.create_index(
        "uq_study_pilot_assignments_enrollment_lineage",
        "study_pilot_assignments",
        ["enrollment_id", "source_lineage_id"],
        unique=True,
    )
    op.create_index(
        "uq_study_pilot_assignments_enrollment_sequence",
        "study_pilot_assignments",
        ["enrollment_id", "sequence_index"],
        unique=True,
    )
    op.create_index(
        "uq_study_pilot_assignments_pair_condition",
        "study_pilot_assignments",
        ["enrollment_id", "pair_index", "condition"],
        unique=True,
    )
    op.create_index(
        "uq_study_pilot_assignments_target_proposal",
        "study_pilot_assignments",
        ["target_proposal_id"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index(
        "uq_study_pilot_assignments_target_proposal",
        table_name="study_pilot_assignments",
    )
    op.drop_index(
        "uq_study_pilot_assignments_pair_condition",
        table_name="study_pilot_assignments",
    )
    op.drop_index(
        "uq_study_pilot_assignments_enrollment_sequence",
        table_name="study_pilot_assignments",
    )
    op.drop_index(
        "uq_study_pilot_assignments_enrollment_lineage",
        table_name="study_pilot_assignments",
    )
    op.drop_table("study_pilot_assignments")

    op.drop_index(
        "uq_study_pilot_enrollments_randomization_seed",
        table_name="study_pilot_enrollments",
    )
    op.drop_index(
        "uq_study_pilot_enrollments_active_user",
        table_name="study_pilot_enrollments",
    )
    op.drop_index(
        "uq_study_pilot_enrollments_user_cohort",
        table_name="study_pilot_enrollments",
    )
    op.drop_table("study_pilot_enrollments")

    op.drop_index(
        "ix_lesson_proposal_audits_source",
        table_name="lesson_proposal_audits",
    )
    op.drop_index(
        "uq_lesson_proposal_audits_proposal",
        table_name="lesson_proposal_audits",
    )
    op.drop_table("lesson_proposal_audits")

    op.drop_index("ix_lesson_checks_user_available", table_name="lesson_checks")
    op.drop_index("ix_lesson_checks_user_status", table_name="lesson_checks")
    op.drop_index("uq_lesson_checks_proposal_kind", table_name="lesson_checks")
    op.drop_table("lesson_checks")

    op.drop_constraint(
        "ck_material_topic_proposals_recall_boundary",
        "material_topic_proposals",
        type_="check",
    )
    op.drop_constraint(
        "ck_material_topic_proposals_exposure_pair",
        "material_topic_proposals",
        type_="check",
    )
    op.drop_column("material_topic_proposals", "recall_not_before_at")
    op.drop_column("material_topic_proposals", "last_learning_exposure_at")

    op.drop_constraint(
        "ck_material_sources_confirmed_time",
        "material_sources",
        type_="check",
    )
    op.drop_constraint(
        "ck_material_sources_review_opened_time",
        "material_sources",
        type_="check",
    )
    op.drop_constraint(
        "ck_material_sources_proposals_ready_time",
        "material_sources",
        type_="check",
    )
    op.drop_column("material_sources", "confirmed_at")
    op.drop_column("material_sources", "review_opened_at")
    op.drop_column("material_sources", "proposals_ready_at")
