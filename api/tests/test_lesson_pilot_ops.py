import json
import stat
import uuid
from datetime import UTC, date, datetime, timedelta

import pytest
from fastapi import HTTPException
from sqlmodel import select

from app.config import get_settings
from app.models import (
    FOUNDER_USER_ID,
    LESSON_CHECK_EXPOSED,
    LESSON_CHECK_FORMATION,
    LESSON_CHECK_SUBMITTED,
    LESSON_CHECK_TRANSFER,
    LESSON_CONDITION_ATTEMPT_FIRST,
    LESSON_CONDITION_RESTUDY,
    LESSON_OUTCOME_MISSING_MECHANISM,
    LESSON_PROMPT_APPLICATION,
    LESSON_PROMPT_CANONICAL,
    PROPOSAL_AUDIT_APPROVED,
    PROPOSAL_AUDIT_PENDING,
    PROPOSAL_CLEAN,
    PROPOSAL_EXCLUDED,
    SOURCE_PENDING,
    SOURCE_READY,
    STATUS_COMPLETE,
    Card,
    LessonCheck,
    LessonProposalAudit,
    LLMUsage,
    MaterialSource,
    MaterialTopicProposal,
    Session,
    User,
)
from app.pilot_contract import (
    PILOT_ASSIGNMENT_ALGORITHM_VERSION,
    PILOT_RESEARCH_CONSENT_VERSION,
)
from app.services import ai_consent, materials, usage
from app.services.lesson_pilot_ops import (
    AssignmentInput,
    PilotOperatorError,
    ProvisionedAssignmentInput,
    approve_transfer_prompt,
    assign_manifest,
    bind_assignment,
    derive_assignment_plan,
    enroll_participant,
    provision_manifest,
    review_proposal,
    start_manifest_processing,
    withdraw_participant,
)
from scripts.lesson_pilot_operator import _provision_manifest
from scripts.lesson_pilot_report import (
    PilotReportError,
    _write_restricted_report,
    build_report,
)
from scripts.lesson_pilot_report import _parser as report_parser


def _rubric() -> dict[str, str]:
    return {
        "mechanism": "Mechanism",
        "acceptable_alternative": "Alternative",
        "trade_off": "Trade-off",
        "failure_mode": "Failure mode",
        "misconception": "Misconception",
    }


def _recall_questions() -> list[dict[str, str]]:
    return [
        {"level": "definition_recognition", "question": "Explain it."},
        {"level": "mechanism", "question": "How does it work?"},
        {"level": "derivation", "question": "What follows from it?"},
        {"level": "application", "question": "Where is it useful?"},
        {"level": "failure_tradeoff", "question": "When does it fail?"},
    ]


def _snapshot() -> dict[str, object]:
    return {
        "assignment_algorithm_version": PILOT_ASSIGNMENT_ALGORITHM_VERSION,
        "extraction_provider_route": {
            "provider": "anthropic",
            "model": "claude-sonnet-5",
            "effort": "low",
        },
        "extraction_prompt_version": "lesson-extraction-v1",
        "grounding_gate_version": "1",
        "formation_prompt_version": "lesson-formation-v1",
        "formation_provider_route": {
            "provider": "anthropic",
            "model": "claude-sonnet-5",
            "effort": "low",
        },
        "restudy_prompt_version": "source-restudy-v1",
        "transfer_prompt_version": "transfer-v1",
        "transfer_prompt_rubric_version": "transfer-rubric-v1",
        "minimum_client_build": 10,
        "pilot_consent_version": PILOT_RESEARCH_CONSENT_VERSION,
    }


async def _assigned_pilot(db):
    now = datetime.now(UTC)
    user = User()
    db.add(user)
    await db.commit()
    enrollment = await enroll_participant(
        db,
        user_id=user.id,
        cohort="pilot-2026-08",
        consent_version=PILOT_RESEARCH_CONSENT_VERSION,
        consented_at=now,
        randomization_seed=f"seed-{user.id}",
    )
    sources = [
        MaterialSource(
            user_id=user.id,
            kind="lesson",
            title=f"Lesson {index}",
            source_text=f"Bounded source {index}",
            import_path="lesson",
            status=SOURCE_PENDING,
        )
        for index in range(1, 7)
    ]
    for source in sources:
        db.add(source)
    await db.commit()
    snapshot = _snapshot()
    derived = derive_assignment_plan(
        enrollment.randomization_seed,
        [
            (source.id, source.lineage_id, ((index - 1) // 2) + 1)
            for index, source in enumerate(sources, 1)
        ],
    )
    entries = [
        AssignmentInput(
            source_id=source.id,
            pair_index=((index - 1) // 2) + 1,
            sequence_index=derived[source.id][0],
            condition=derived[source.id][1],
            intended_target="position:1",
            version_snapshot=snapshot,
        )
        for index, source in enumerate(sources, 1)
    ]
    assignments = await assign_manifest(
        db, enrollment_id=enrollment.id, entries=entries
    )
    return user, enrollment, sources, assignments, entries


async def test_enrollment_requires_supported_current_consent_and_real_timestamp(db):
    user = User()
    db.add(user)
    await db.commit()

    with pytest.raises(PilotOperatorError, match="consent version is not supported"):
        await enroll_participant(
            db,
            user_id=user.id,
            cohort="pilot-2026-08",
            consent_version="research-v999",
            consented_at=datetime.now(UTC),
            randomization_seed=f"unsupported-{user.id}",
        )

    with pytest.raises(PilotOperatorError, match="meaningfully in the future"):
        await enroll_participant(
            db,
            user_id=user.id,
            cohort="pilot-2026-08",
            consent_version=PILOT_RESEARCH_CONSENT_VERSION,
            consented_at=datetime.now(UTC) + timedelta(hours=1),
            randomization_seed=f"future-{user.id}",
        )


async def test_operator_provisions_frozen_drafts_before_explicit_processing_release(db):
    now = datetime.now(UTC)
    user = User()
    db.add(user)
    await db.commit()
    enrollment = await enroll_participant(
        db,
        user_id=user.id,
        cohort="pilot-2026-08",
        consent_version=PILOT_RESEARCH_CONSENT_VERSION,
        consented_at=now,
        randomization_seed=f"provision-seed-{user.id}",
    )
    entries = [
        ProvisionedAssignmentInput(
            source_id=uuid.uuid4(),
            source_lineage_id=uuid.uuid4(),
            title=f"Pilot source {index}",
            source_text=(f"Bounded consented source {index}. " * 20),
            source_url="",
            content_provenance="learner_notes",
            kind="notes",
            original_filename="",
            mime_type="text/plain",
            intent="learn",
            pair_index=((index - 1) // 2) + 1,
            intended_target="position:1",
            version_snapshot=_snapshot(),
        )
        for index in range(1, 7)
    ]

    sources, assignments = await provision_manifest(
        db,
        enrollment_id=enrollment.id,
        entries=entries,
    )
    assert len(sources) == len(assignments) == 6
    assert {source.status for source in sources} == {"draft"}
    assert all(source.processing_run_id is None for source in sources)
    assert [row.sequence_index for row in assignments] == list(range(1, 7))
    assert {
        (row.pair_index, row.condition) for row in assignments
    } == {
        (1, LESSON_CONDITION_ATTEMPT_FIRST),
        (1, LESSON_CONDITION_RESTUDY),
        (2, LESSON_CONDITION_ATTEMPT_FIRST),
        (2, LESSON_CONDITION_RESTUDY),
        (3, LESSON_CONDITION_ATTEMPT_FIRST),
        (3, LESSON_CONDITION_RESTUDY),
    }
    replay_sources, replay_assignments = await provision_manifest(
        db,
        enrollment_id=enrollment.id,
        entries=entries,
    )
    assert [row.id for row in replay_sources] == [row.id for row in sources]
    assert [row.id for row in replay_assignments] == [row.id for row in assignments]

    source_ids = await start_manifest_processing(db, enrollment_id=enrollment.id)
    assert source_ids == [row.source_id for row in assignments]
    for source in sources:
        await db.refresh(source)
    assert {source.status for source in sources} == {SOURCE_PENDING}
    assert await start_manifest_processing(db, enrollment_id=enrollment.id) == source_ids


async def test_operator_freezes_all_six_assignments_atomically_and_idempotently(db):
    _, enrollment, _, assignments, entries = await _assigned_pilot(db)

    assert [row.sequence_index for row in assignments] == list(range(1, 7))
    assert {
        (row.pair_index, row.condition) for row in assignments
    } == {
        (1, LESSON_CONDITION_ATTEMPT_FIRST),
        (1, LESSON_CONDITION_RESTUDY),
        (2, LESSON_CONDITION_ATTEMPT_FIRST),
        (2, LESSON_CONDITION_RESTUDY),
        (3, LESSON_CONDITION_ATTEMPT_FIRST),
        (3, LESSON_CONDITION_RESTUDY),
    }
    replay = await assign_manifest(db, enrollment_id=enrollment.id, entries=entries)
    assert [row.id for row in replay] == [row.id for row in assignments]

    pair = [entry for entry in entries if entry.pair_index == 1]
    flipped_by_source = {
        pair[0].source_id: pair[1].condition,
        pair[1].source_id: pair[0].condition,
    }
    flipped = [
        AssignmentInput(
            source_id=entry.source_id,
            pair_index=entry.pair_index,
            sequence_index=entry.sequence_index,
            condition=flipped_by_source.get(entry.source_id, entry.condition),
            intended_target=entry.intended_target,
            version_snapshot=entry.version_snapshot,
        )
        for entry in entries
    ]
    with pytest.raises(PilotOperatorError, match="randomization seed"):
        await assign_manifest(db, enrollment_id=enrollment.id, entries=flipped)

    first, second = entries[:2]
    swapped_sequence = [
        AssignmentInput(
            source_id=entry.source_id,
            pair_index=entry.pair_index,
            sequence_index=(
                second.sequence_index
                if entry.source_id == first.source_id
                else first.sequence_index
                if entry.source_id == second.source_id
                else entry.sequence_index
            ),
            condition=entry.condition,
            intended_target=entry.intended_target,
            version_snapshot=entry.version_snapshot,
        )
        for entry in entries
    ]
    with pytest.raises(PilotOperatorError, match="randomization seed"):
        await assign_manifest(
            db,
            enrollment_id=enrollment.id,
            entries=swapped_sequence,
        )

    changed = [*entries]
    changed[0] = AssignmentInput(
        source_id=entries[0].source_id,
        pair_index=entries[0].pair_index,
        sequence_index=entries[0].sequence_index,
        condition=entries[0].condition,
        intended_target="position:2",
        version_snapshot=entries[0].version_snapshot,
    )
    with pytest.raises(PilotOperatorError, match="different frozen manifest"):
        await assign_manifest(db, enrollment_id=enrollment.id, entries=changed)

    missing_snapshot = dict(entries[0].version_snapshot)
    missing_snapshot.pop("restudy_prompt_version")
    missing = [*entries]
    missing[0] = AssignmentInput(
        source_id=entries[0].source_id,
        pair_index=entries[0].pair_index,
        sequence_index=entries[0].sequence_index,
        condition=entries[0].condition,
        intended_target=entries[0].intended_target,
        version_snapshot=missing_snapshot,
    )
    with pytest.raises(PilotOperatorError, match="missing frozen keys"):
        await assign_manifest(db, enrollment_id=enrollment.id, entries=missing)

    changed_contract = dict(entries[1].version_snapshot)
    changed_contract["minimum_client_build"] = 11
    mixed = [*entries]
    mixed[1] = AssignmentInput(
        source_id=entries[1].source_id,
        pair_index=entries[1].pair_index,
        sequence_index=entries[1].sequence_index,
        condition=entries[1].condition,
        intended_target=entries[1].intended_target,
        version_snapshot=changed_contract,
    )
    with pytest.raises(PilotOperatorError, match="one frozen contract snapshot"):
        await assign_manifest(db, enrollment_id=enrollment.id, entries=mixed)

    private_target = [*entries]
    private_target[0] = AssignmentInput(
        source_id=entries[0].source_id,
        pair_index=entries[0].pair_index,
        sequence_index=entries[0].sequence_index,
        condition=entries[0].condition,
        intended_target="topic:Private concept name",
        version_snapshot=entries[0].version_snapshot,
    )
    with pytest.raises(PilotOperatorError, match="intended_target must be position"):
        await assign_manifest(
            db,
            enrollment_id=enrollment.id,
            entries=private_target,
        )


def test_provision_manifest_refuses_operator_selected_condition_or_sequence(tmp_path):
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps(
            [
                {
                    "source_id": str(uuid.uuid4()),
                    "source_lineage_id": str(uuid.uuid4()),
                    "title": "Private title",
                    "source_text": "Private bounded source",
                    "content_provenance": "learner_notes",
                    "pair_index": 1,
                    "condition": LESSON_CONDITION_ATTEMPT_FIRST,
                    "intended_target": "position:1",
                    "version_snapshot": _snapshot(),
                }
            ]
        ),
        encoding="utf-8",
    )

    with pytest.raises(PilotOperatorError, match="derived from the enrollment seed"):
        _provision_manifest(manifest)


async def test_pilot_import_lane_keeps_total_cap_but_not_public_three_import_cap(db):
    now = datetime.now(UTC)
    for _ in range(3):
        db.add(
            LLMUsage(
                user_id=FOUNDER_USER_ID,
                operation="guide_import",
                created_at=now,
            )
        )
    await db.commit()
    config = get_settings().model_copy(
        update={
            "ai_consent_enforcement_enabled": False,
            "guide_imports_per_day": 3,
            "llm_calls_per_day": 4,
        }
    )

    with pytest.raises(HTTPException) as public_limit:
        await usage.ensure_available(db, FOUNDER_USER_ID, "guide_import", config)
    assert public_limit.value.detail == "daily_import_limit"
    await db.rollback()

    await usage.ensure_available(
        db,
        FOUNDER_USER_ID,
        "lesson_pilot_import",
        config,
    )
    db.add(
        LLMUsage(
            user_id=FOUNDER_USER_ID,
            operation="lesson_pilot_import",
            created_at=now,
        )
    )
    await db.commit()
    with pytest.raises(HTTPException) as total_limit:
        await usage.ensure_available(
            db,
            FOUNDER_USER_ID,
            "lesson_pilot_import",
            config,
        )
    assert total_limit.value.detail == "daily_model_limit"


async def test_assigned_pilot_import_authorizer_rechecks_consent_and_live_claim(
    db, monkeypatch
):
    user, enrollment, sources, _, _ = await _assigned_pilot(db)
    source = sources[0]
    run_id = uuid.uuid4()
    source.status = "processing"
    source.processing_run_id = run_id
    source.processing_heartbeat_at = datetime.now(UTC)
    db.add(source)
    await ai_consent.record(
        db,
        user.id,
        "grant",
        ai_consent.POLICY_VERSION,
    )
    enforced = get_settings().model_copy(
        update={"ai_consent_enforcement_enabled": True}
    )
    monkeypatch.setattr(materials, "get_settings", lambda: enforced)

    authorize = materials._guide_authorizer(
        db,
        user.id,
        source.id,
        run_id,
        model="claude-sonnet-5",
        operation=materials.PILOT_LESSON_IMPORT_OPERATION,
    )
    await authorize(1)

    rows = (
        await db.exec(
            select(LLMUsage).where(
                LLMUsage.user_id == user.id,
                LLMUsage.operation == materials.PILOT_LESSON_IMPORT_OPERATION,
            )
        )
    ).all()
    assert len(rows) == 1
    assert rows[0].details["ai_consent_verified"] is True
    assert rows[0].details["logical_operation"] == "lesson_pilot_import"

    enrollment.consent_version = "unsupported-pilot-consent"
    db.add(enrollment)
    await db.commit()
    with pytest.raises(HTTPException, match="pilot consent invalid"):
        await authorize(1)


async def test_operator_review_preserves_original_evidence_and_bind_excludes_non_target(db):
    _, _, sources, assignments, _ = await _assigned_pilot(db)
    source = sources[0]
    assignment = next(row for row in assignments if row.source_id == source.id)
    source.status = SOURCE_READY
    source.proposals_ready_at = datetime.now(UTC)
    source.result_summary = {"grounding_gate_version": 1}
    first = MaterialTopicProposal(
        source_id=source.id,
        position=1,
        section_title="Section 1",
        topic="Target concept",
        answer_anchor="Target answer basis",
        source_excerpt="Target excerpt",
        canonical_question="Explain the target concept.",
        answer_rubric=_rubric(),
        recall_questions=_recall_questions(),
        status=PROPOSAL_CLEAN,
    )
    second = MaterialTopicProposal(
        source_id=source.id,
        position=2,
        section_title="Section 2",
        topic="Non-target concept",
        answer_anchor="Non-target answer basis",
        source_excerpt="Non-target excerpt",
        canonical_question="Explain the non-target concept.",
        answer_rubric=_rubric(),
        recall_questions=_recall_questions(),
        status=PROPOSAL_CLEAN,
    )
    db.add(source)
    db.add(first)
    db.add(second)
    await db.flush()
    original_packs = {
        first.id: {"topic": "Original target output"},
        second.id: {"topic": "Original non-target output"},
    }
    for proposal in (first, second):
        db.add(
            LessonProposalAudit(
                source_id=source.id,
                proposal_id=proposal.id,
                extraction_route=_snapshot()["extraction_provider_route"],
                extraction_prompt_version="lesson-extraction-v1",
                grounding_gate_version="1",
                original_proposal_pack=original_packs[proposal.id],
                original_grounding_findings=[
                    {"field": "answer_basis", "status": "supported"}
                ],
                reviewer_decision=PROPOSAL_AUDIT_PENDING,
            )
        )
    await db.commit()

    await review_proposal(
        db,
        proposal_id=first.id,
        reviewer_id="reviewer-01",
        decision=PROPOSAL_AUDIT_APPROVED,
    )
    corrected_audit = await review_proposal(
        db,
        proposal_id=second.id,
        reviewer_id="reviewer-01",
        decision="corrected",
        correction={
            "section_title": "Corrected section",
            "topic": "Corrected non-target concept",
            "answer_basis": "A complete corrected source-backed account.",
            "source_excerpt": "Bounded source 1",
            "canonical_question": "How does the corrected concept work?",
            "answer_rubric": _rubric(),
            "recall_questions": [
                {
                    "level": "definition_recognition",
                    "question": "What is the corrected concept?",
                },
                {
                    "level": "mechanism",
                    "question": "How does the corrected mechanism operate?",
                },
                {
                    "level": "derivation",
                    "question": "What follows from the corrected mechanism?",
                },
                {
                    "level": "application",
                    "question": "Where would the corrected concept apply?",
                },
                {
                    "level": "failure_tradeoff",
                    "question": "When would the corrected concept fail?",
                },
            ],
        },
    )
    assert corrected_audit.original_proposal_pack == original_packs[second.id]
    assert corrected_audit.reviewer_correction["topic"] == (
        "Corrected non-target concept"
    )
    target_audit = await review_proposal(
        db,
        proposal_id=first.id,
        reviewer_id="reviewer-01",
        decision=PROPOSAL_AUDIT_APPROVED,
    )
    assert target_audit.original_proposal_pack == original_packs[first.id]
    with pytest.raises(PilotOperatorError, match="immutable"):
        await review_proposal(
            db,
            proposal_id=first.id,
            reviewer_id="reviewer-02",
            decision=PROPOSAL_AUDIT_APPROVED,
        )

    target_audit.extraction_route = {
        "provider": "anthropic",
        "model": "post-hoc-model",
        "effort": "low",
    }
    db.add(target_audit)
    await db.commit()
    with pytest.raises(PilotOperatorError, match="frozen extraction contract"):
        await bind_assignment(
            db,
            assignment_id=assignment.id,
            proposal_id=first.id,
        )
    target_audit.extraction_route = _snapshot()["extraction_provider_route"]
    db.add(target_audit)
    await db.commit()

    with pytest.raises(PilotOperatorError, match="predeclared deterministic target"):
        await bind_assignment(
            db,
            assignment_id=assignment.id,
            proposal_id=second.id,
        )

    bound = await bind_assignment(
        db,
        assignment_id=assignment.id,
        proposal_id=first.id,
    )
    await db.refresh(second)
    assert bound.target_proposal_id == first.id
    assert bound.bound_at is not None
    assert second.status == PROPOSAL_EXCLUDED
    assert second.issue == "pilot_non_target"

    replay = await bind_assignment(
        db,
        assignment_id=assignment.id,
        proposal_id=first.id,
    )
    assert replay.bound_at == bound.bound_at

    approved_at = bound.bound_at + timedelta(seconds=1)
    transfer = await approve_transfer_prompt(
        db,
        assignment_id=bound.id,
        candidate_index=4,
        reviewer_id="transfer-reviewer-01",
        approved_at=approved_at,
    )
    assert transfer.kind == LESSON_CHECK_TRANSFER
    assert transfer.prompt_level == LESSON_PROMPT_APPLICATION
    assert transfer.source_candidate_id == "recall_questions:4"
    assert transfer.prompt_text_snapshot == "Where is it useful?"
    assert transfer.answer_text == ""
    assert transfer.exposed_at is None
    transfer_replay = await approve_transfer_prompt(
        db,
        assignment_id=bound.id,
        candidate_index=4,
        reviewer_id="transfer-reviewer-01",
        approved_at=approved_at,
    )
    assert transfer_replay.id == transfer.id


async def test_participant_report_excludes_private_content_scores_and_withdrawn_rows(db):
    user, enrollment, sources, assignments, _ = await _assigned_pilot(db)
    source = sources[0]
    now = datetime.now(UTC)
    exposed_at = now - timedelta(days=8)
    recall_gate = exposed_at + timedelta(days=1)
    source.status = SOURCE_READY
    source.created_at = exposed_at - timedelta(hours=1)
    source.proposals_ready_at = exposed_at
    source.review_opened_at = exposed_at
    source.confirmed_at = exposed_at
    source.updated_at = now
    proposal = MaterialTopicProposal(
        source_id=source.id,
        position=1,
        section_title="ULTRA_PRIVATE section",
        topic="ULTRA_PRIVATE topic",
        answer_anchor="ULTRA_PRIVATE authority",
        source_excerpt="ULTRA_PRIVATE source excerpt",
        canonical_question="ULTRA_PRIVATE question",
        answer_rubric=_rubric(),
        recall_questions=_recall_questions(),
        status=PROPOSAL_CLEAN,
    )
    db.add(source)
    db.add(proposal)
    await db.flush()
    card = Card(
        user_id=user.id,
        topic="ULTRA_PRIVATE card",
        category="Private",
        canonical_question="ULTRA_PRIVATE question",
        answer_anchor="ULTRA_PRIVATE answer",
        next_review_at=date.today(),
        last_score=5,
        last_learning_exposure_at=exposed_at,
        recall_not_before_at=recall_gate,
    )
    db.add(card)
    proposal.card_id = card.id
    assignment = next(row for row in assignments if row.source_id == source.id)
    assignment.target_proposal_id = proposal.id
    assignment.assigned_at = exposed_at - timedelta(minutes=1)
    assignment.bound_at = exposed_at
    assignment.updated_at = now
    db.add(assignment)
    audit = LessonProposalAudit(
        source_id=source.id,
        proposal_id=proposal.id,
        extraction_route={"provider": "anthropic", "model": "ULTRA_PRIVATE model"},
        extraction_prompt_version="lesson-extraction-v1",
        grounding_gate_version="1",
        original_proposal_pack={"private": "ULTRA_PRIVATE original model output"},
        original_grounding_findings=[{"private": "ULTRA_PRIVATE finding"}],
        reviewer_id="reviewer-01",
        reviewer_decision=PROPOSAL_AUDIT_APPROVED,
        reviewed_at=exposed_at,
    )
    formation = LessonCheck(
        user_id=user.id,
        proposal_id=proposal.id,
        card_id=card.id,
        kind=LESSON_CHECK_FORMATION,
        condition=LESSON_CONDITION_ATTEMPT_FIRST,
        prompt_level=LESSON_PROMPT_CANONICAL,
        prompt_version="formation-v1",
        provider_route={"model": "ULTRA_PRIVATE model"},
        prompt_text_snapshot="ULTRA_PRIVATE prompt",
        status=LESSON_CHECK_EXPOSED,
        draft_text="ULTRA_PRIVATE draft",
        answer_text="ULTRA_PRIVATE formation answer",
        qualitative_outcome=LESSON_OUTCOME_MISSING_MECHANISM,
        feedback="ULTRA_PRIVATE feedback",
        exposed_at=exposed_at,
        recall_not_before_at=recall_gate,
        started_at=exposed_at,
        submitted_at=exposed_at,
        updated_at=exposed_at,
    )
    transfer = LessonCheck(
        user_id=user.id,
        proposal_id=proposal.id,
        card_id=card.id,
        kind=LESSON_CHECK_TRANSFER,
        prompt_level=LESSON_PROMPT_APPLICATION,
        prompt_version="transfer-v1",
        prompt_text_snapshot="ULTRA_PRIVATE transfer prompt",
        status=LESSON_CHECK_SUBMITTED,
        draft_text="ULTRA_PRIVATE transfer draft",
        answer_text="ULTRA_PRIVATE transfer answer",
        available_at=exposed_at + timedelta(days=7),
        started_at=now - timedelta(hours=1),
        submitted_at=now,
        updated_at=now,
    )
    recall = Session(
        card_id=card.id,
        question_asked="ULTRA_PRIVATE recall question",
        answer_text="ULTRA_PRIVATE recall transcript",
        score=5,
        accuracy=5,
        depth=5,
        boundaries=5,
        feedback="ULTRA_PRIVATE score feedback",
        status=STATUS_COMPLETE,
        started_at=recall_gate + timedelta(hours=1),
        ended_at=recall_gate + timedelta(hours=1, minutes=1),
    )
    db.add(audit)
    db.add(formation)
    db.add(transfer)
    db.add(recall)
    await db.commit()

    report = await build_report(
        db,
        enrollment_id=enrollment.id,
        generated_at=now,
    )
    serialized = json.dumps(report, sort_keys=True)
    assert "ULTRA_PRIVATE" not in serialized
    participant = report["participants"][0]
    assert participant["formation_completed_count"] == 1
    assert participant["first_recall_completed_count"] == 1
    assert participant["transfer_submitted_count"] == 1
    reported_assignment = next(
        row
        for row in participant["assignments"]
        if row["assignment_id"] == str(assignment.id)
    )
    assert reported_assignment["formation"]["qualitative_outcome"] == (
        LESSON_OUTCOME_MISSING_MECHANISM
    )
    assert reported_assignment["first_recall"]["session_id"] == str(recall.id)
    assert "score" not in serialized

    withdrawn_at = now + timedelta(minutes=1)
    await withdraw_participant(
        db,
        enrollment_id=enrollment.id,
        withdrawn_at=withdrawn_at,
    )
    with pytest.raises(PilotReportError, match="active enrollment not found"):
        await build_report(db, enrollment_id=enrollment.id)


def test_restricted_report_requires_new_explicit_mode_0600_output(tmp_path):
    with pytest.raises(SystemExit):
        report_parser().parse_args([])

    destination = tmp_path / "participant-report.json"
    payload = '{"participants":[]}\n'
    _write_restricted_report(destination, payload)

    assert destination.read_text(encoding="utf-8") == payload
    assert stat.S_IMODE(destination.stat().st_mode) == 0o600
    with pytest.raises(PilotReportError, match="refusing to overwrite"):
        _write_restricted_report(destination, '{"changed":true}\n')
    assert destination.read_text(encoding="utf-8") == payload
