import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlmodel import select

from app.models import (
    FOUNDER_USER_ID,
    LESSON_CHECK_EXPOSED,
    LESSON_CHECK_FORMATION,
    LESSON_CHECK_SUBMITTED,
    LESSON_CHECK_TRANSFER,
    LESSON_CONDITION_ATTEMPT_FIRST,
    LESSON_OUTCOME_ACCURATE_ACCOUNT,
    LESSON_PROMPT_APPLICATION,
    LESSON_PROMPT_CANONICAL,
    PILOT_CONDITION_ATTEMPT_FIRST,
    PILOT_CONDITION_RESTUDY,
    PROPOSAL_AUDIT_APPROVED,
    SOURCE_READY,
    LessonCheck,
    LessonProposalAudit,
    MaterialSource,
    MaterialTopicProposal,
    Session,
    StudyPilotAssignment,
    StudyPilotEnrollment,
)


def _source(created_at: datetime) -> MaterialSource:
    return MaterialSource(
        user_id=FOUNDER_USER_ID,
        title="Request routing",
        source_text="A request passes through named routing stages.",
        import_path="lesson",
        intent="learn",
        status=SOURCE_READY,
        created_at=created_at,
        updated_at=created_at,
    )


def _proposal(source: MaterialSource) -> MaterialTopicProposal:
    return MaterialTopicProposal(
        source_id=source.id,
        position=1,
        section_title="Request path",
        topic="Request routing",
        answer_anchor="The request passes through named routing stages.",
        source_excerpt="A request passes through named routing stages.",
        canonical_question="How does the request route through the system?",
        answer_rubric={"mechanism": "Names the stages in order."},
        recall_questions=[
            {
                "level": "application",
                "question": "Where would you add a routing constraint?",
            }
        ],
    )


def _enrollment(now: datetime) -> StudyPilotEnrollment:
    return StudyPilotEnrollment(
        user_id=FOUNDER_USER_ID,
        cohort="pilot-2026-08",
        consent_version="pilot-consent-v1",
        consented_at=now,
        randomization_seed=f"seed-{uuid.uuid4()}",
    )


@pytest.mark.asyncio
async def test_pilot_models_store_unscored_proposal_owned_state(db):
    now = datetime.now(UTC)
    gate = now + timedelta(hours=8)
    source = _source(now - timedelta(minutes=15))
    source.proposals_ready_at = now - timedelta(minutes=5)
    source.review_opened_at = now - timedelta(minutes=3)
    source.confirmed_at = now
    source.updated_at = now
    proposal = _proposal(source)
    proposal.last_learning_exposure_at = now
    proposal.recall_not_before_at = gate
    enrollment = _enrollment(now - timedelta(days=1))

    db.add(source)
    db.add(proposal)
    db.add(enrollment)
    await db.flush()

    assignment = StudyPilotAssignment(
        enrollment_id=enrollment.id,
        source_lineage_id=source.lineage_id,
        source_id=source.id,
        pair_index=1,
        sequence_index=1,
        condition=PILOT_CONDITION_ATTEMPT_FIRST,
        intended_target="position:1",
        target_proposal_id=proposal.id,
        version_snapshot={
            "extraction_prompt": "lesson-extract-v1",
            "formation_prompt": "formation-v1",
            "model": "frozen-model",
        },
        assigned_at=now - timedelta(minutes=10),
        bound_at=now - timedelta(minutes=4),
        updated_at=now - timedelta(minutes=4),
    )
    audit = LessonProposalAudit(
        source_id=source.id,
        proposal_id=proposal.id,
        extraction_route={"provider": "anthropic", "model": "frozen-model"},
        extraction_prompt_version="lesson-extract-v1",
        grounding_gate_version="lesson-grounding-v1",
        original_proposal_pack={"topic": proposal.topic},
        original_grounding_findings=[],
        reviewer_id="reviewer-1",
        reviewer_decision=PROPOSAL_AUDIT_APPROVED,
        reviewed_at=now - timedelta(minutes=4),
    )
    check = LessonCheck(
        user_id=FOUNDER_USER_ID,
        proposal_id=proposal.id,
        kind=LESSON_CHECK_FORMATION,
        condition=LESSON_CONDITION_ATTEMPT_FIRST,
        prompt_level=LESSON_PROMPT_CANONICAL,
        prompt_version="formation-v1",
        provider_route={"provider": "anthropic", "model": "frozen-model"},
        prompt_text_snapshot=proposal.canonical_question,
        prompt_rubric_version="formation-rubric-v1",
        status=LESSON_CHECK_EXPOSED,
        answer_text="It passes through named routing stages.",
        qualitative_outcome=LESSON_OUTCOME_ACCURATE_ACCOUNT,
        feedback="Accurate source-backed account.",
        exposed_at=now,
        recall_not_before_at=gate,
        started_at=now - timedelta(minutes=1),
        submitted_at=now,
    )
    db.add_all([assignment, audit, check])
    await db.commit()

    stored_check = await db.get(LessonCheck, check.id)
    stored_assignment = await db.get(StudyPilotAssignment, assignment.id)
    stored_audit = await db.get(LessonProposalAudit, audit.id)
    assert stored_check is not None
    assert stored_check.provider_route["model"] == "frozen-model"
    assert stored_check.recall_not_before_at is not None
    assert stored_assignment is not None
    assert stored_assignment.source_lineage_id == source.lineage_id
    assert stored_assignment.version_snapshot["formation_prompt"] == "formation-v1"
    assert stored_audit is not None
    assert stored_audit.original_proposal_pack == {"topic": "Request routing"}
    assert (await db.exec(select(Session))).all() == []


@pytest.mark.asyncio
async def test_check_and_assignment_cardinality_is_structural(db):
    now = datetime.now(UTC)
    source = _source(now)
    proposal = _proposal(source)
    enrollment = _enrollment(now)
    db.add_all([source, proposal, enrollment])
    await db.flush()

    formation = LessonCheck(
        user_id=FOUNDER_USER_ID,
        proposal_id=proposal.id,
        kind=LESSON_CHECK_FORMATION,
        condition=LESSON_CONDITION_ATTEMPT_FIRST,
        prompt_level=LESSON_PROMPT_CANONICAL,
        prompt_version="formation-v1",
        prompt_text_snapshot=proposal.canonical_question,
        started_at=now,
    )
    transfer = LessonCheck(
        user_id=FOUNDER_USER_ID,
        proposal_id=proposal.id,
        kind=LESSON_CHECK_TRANSFER,
        prompt_level=LESSON_PROMPT_APPLICATION,
        prompt_version="transfer-v1",
        prompt_text_snapshot="Where would you add a routing constraint?",
        prompt_rubric_version="transfer-rubric-v1",
        status=LESSON_CHECK_SUBMITTED,
        answer_text="Before the storage boundary.",
        available_at=now,
        started_at=now,
        submitted_at=now,
    )
    assignment = StudyPilotAssignment(
        enrollment_id=enrollment.id,
        source_lineage_id=source.lineage_id,
        source_id=source.id,
        pair_index=1,
        sequence_index=1,
        condition=PILOT_CONDITION_ATTEMPT_FIRST,
        intended_target="position:1",
        target_proposal_id=proposal.id,
        version_snapshot={"formation_prompt": "formation-v1"},
        assigned_at=now,
        bound_at=now,
        updated_at=now,
    )
    db.add_all([formation, transfer, assignment])
    await db.commit()
    enrollment_id = enrollment.id
    source_id = source.id
    source_lineage_id = source.lineage_id

    duplicate_formation = LessonCheck(
        user_id=FOUNDER_USER_ID,
        proposal_id=proposal.id,
        kind=LESSON_CHECK_FORMATION,
        condition=LESSON_CONDITION_ATTEMPT_FIRST,
        prompt_level=LESSON_PROMPT_CANONICAL,
        prompt_version="formation-v1",
        prompt_text_snapshot=proposal.canonical_question,
        started_at=now,
    )
    db.add(duplicate_formation)
    with pytest.raises(IntegrityError):
        await db.commit()
    await db.rollback()

    duplicate_lineage = StudyPilotAssignment(
        enrollment_id=enrollment_id,
        source_lineage_id=source_lineage_id,
        source_id=source_id,
        pair_index=2,
        sequence_index=2,
        condition=PILOT_CONDITION_RESTUDY,
        intended_target="position:1",
        version_snapshot={"formation_prompt": "formation-v1"},
        assigned_at=now,
        updated_at=now,
    )
    db.add(duplicate_lineage)
    with pytest.raises(IntegrityError):
        await db.commit()
    await db.rollback()


@pytest.mark.asyncio
async def test_source_deletion_removes_private_checks_but_keeps_assignment(db):
    if db.bind.dialect.name == "sqlite":
        await db.exec(text("PRAGMA foreign_keys=ON"))
    now = datetime.now(UTC)
    source = _source(now)
    proposal = _proposal(source)
    enrollment = _enrollment(now)
    db.add_all([source, proposal, enrollment])
    await db.flush()
    check = LessonCheck(
        user_id=FOUNDER_USER_ID,
        proposal_id=proposal.id,
        kind=LESSON_CHECK_FORMATION,
        condition=LESSON_CONDITION_ATTEMPT_FIRST,
        prompt_level=LESSON_PROMPT_CANONICAL,
        prompt_version="formation-v1",
        prompt_text_snapshot=proposal.canonical_question,
        started_at=now,
    )
    audit = LessonProposalAudit(
        source_id=source.id,
        proposal_id=proposal.id,
        extraction_prompt_version="lesson-extract-v1",
        grounding_gate_version="lesson-grounding-v1",
    )
    assignment = StudyPilotAssignment(
        enrollment_id=enrollment.id,
        source_lineage_id=source.lineage_id,
        source_id=source.id,
        pair_index=1,
        sequence_index=1,
        condition=PILOT_CONDITION_ATTEMPT_FIRST,
        intended_target="position:1",
        target_proposal_id=proposal.id,
        version_snapshot={"formation_prompt": "formation-v1"},
        assigned_at=now,
        bound_at=now,
        updated_at=now,
    )
    db.add_all([check, audit, assignment])
    await db.commit()
    proposal_id = proposal.id
    check_id = check.id
    audit_id = audit.id
    assignment_id = assignment.id
    enrollment_id = enrollment.id

    await db.delete(source)
    await db.commit()
    db.expire_all()

    assert await db.get(MaterialTopicProposal, proposal_id) is None
    assert await db.get(LessonCheck, check_id) is None
    assert await db.get(LessonProposalAudit, audit_id) is None
    stored_assignment = await db.get(StudyPilotAssignment, assignment_id)
    assert stored_assignment is not None
    assert stored_assignment.source_id is None
    assert stored_assignment.target_proposal_id is None
    assert stored_assignment.intended_target == "position:1"
    assert "Request routing" not in stored_assignment.intended_target
    assert await db.get(StudyPilotEnrollment, enrollment_id) is not None
