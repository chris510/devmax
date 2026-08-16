import asyncio
import uuid
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, Response
from sqlalchemy import func
from sqlalchemy.orm import defer
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.auth import current_user_id
from app.config import get_settings
from app.db import get_session
from app.models import (
    CONTENT_PROVENANCE_LEGACY_UNSPECIFIED,
    DELIVERY_CONVERSATIONAL,
    LESSON_CHECK_EXPOSED,
    LESSON_CHECK_FORMATION,
    LESSON_CHECK_OPEN,
    LESSON_CHECK_SUBMITTED,
    LESSON_CHECK_TRANSFER,
    LESSON_CONDITION_ATTEMPT_FIRST,
    LESSON_CONDITION_RESTUDY,
    LESSON_PROMPT_CANONICAL,
    LIVE_STATUSES,
    PROPOSAL_AUDIT_APPROVED,
    PROPOSAL_AUDIT_CORRECTED,
    PROPOSAL_CLEAN,
    PROPOSAL_CONFIRMED,
    PROPOSAL_EXCLUDED,
    PROPOSAL_NEEDS_ATTENTION,
    SOURCE_CONFIRMED,
    SOURCE_FAILED,
    SOURCE_NEEDS_ATTENTION,
    SOURCE_PENDING,
    SOURCE_READY,
    SOURCE_SUPERSEDED,
    STATUS_COMPLETE,
    Card,
    LessonCheck,
    LessonProposalAudit,
    LLMUsage,
    MaterialSource,
    MaterialTopicProposal,
    Session,
    StudyPilotAssignment,
    StudyPilotEnrollment,
)
from app.pilot_contract import (
    PILOT_MINIMUM_CLIENT_BUILD,
    RESTUDY_PROMPT_VERSION,
    TRANSFER_DEBRIEF_BOUNDARY_KEY,
    TRANSFER_DEBRIEF_EXPOSURE_KEY,
    TRANSFER_OPENED_AT_KEY,
    TRANSFER_PROMPT_RUBRIC_VERSION,
    TRANSFER_PROMPT_VERSION,
    TRANSFER_QUALIFIED_RECALL_BOUNDARY_KEY,
    pilot_consent_is_valid,
)
from app.routers.deps import as_utc, get_settings_row, local_today, now_in
from app.schemas import (
    CollectionDetail,
    CollectionSummary,
    LearningNoteConcept,
    LessonCheckDraftIn,
    LessonCheckOut,
    LessonCheckSubmitIn,
    LessonConceptProgress,
    LessonProgressOut,
    LessonQuizResult,
    ManualMaterialIn,
    ManualTopicIn,
    MaterialArtifactsOut,
    MaterialConfirmIn,
    MaterialConfirmOut,
    MaterialImportIn,
    MaterialImportOut,
    MaterialImportPreviewOut,
    MaterialTopicAuthorityOut,
    MaterialTopicEdit,
    MaterialTopicOut,
    MaterialTopicPreviewOut,
)
from app.services import llm, materials, second_brain, storage, study_plan, usage
from app.services.card_lifecycle import (
    Grounding,
    GroundingError,
    build_grounded_card,
    lock_topic_creation,
)
from app.services.cards import learning_exposure_boundary
from app.services.scoring_contract import project_card_score
from app.services.scoring_provider import ScoringTrace

router = APIRouter(prefix="/materials", tags=["study material"])

LESSON_GROUNDING_RECOVERY_MARKER = "lesson_grounding_recovery_required"
TRANSFER_DELAY = timedelta(days=7)
_LESSON_CHECK_SUBMIT_LOCKS: dict[uuid.UUID, asyncio.Lock] = {}
AUTHORITY_CONFIRMATION_TITLE = "Choose what becomes a card"
AUTHORITY_CONFIRMATION_MESSAGE = (
    "Keep creates a source-grounded card and preserves this learning hold. "
    "This formation activity is unscored; Recall begins only after the hold opens."
)

COLLECTION = CollectionDetail(
    id="system-design-foundations",
    title="System design foundations",
    subtitle="Core mechanisms and design decisions for software-engineering interviews.",
    version="1.0",
    topic_count=6,
    sections=[
        "Request and data foundations",
        "Concrete technologies",
        "Patterns and application",
    ],
    source_note="Reviewed against the Devmax system-design curriculum.",
    topics=[
        ManualTopicIn(
            topic="Consistent hashing",
            answer_anchor=(
                "Hash keys and nodes onto a ring; virtual nodes balance ownership "
                "and limit movement when membership changes."
            ),
        ),
        ManualTopicIn(
            topic="Database replication",
            answer_anchor=(
                "Replicas copy an ordered write history; consistency, failover, and "
                "lag depend on acknowledgement and leadership rules."
            ),
        ),
        ManualTopicIn(
            topic="Cache invalidation",
            answer_anchor=(
                "Cached data needs an explicit freshness policy using expiry, "
                "versioning, or invalidation, with races handled at write boundaries."
            ),
        ),
        ManualTopicIn(
            topic="Rate limiting",
            answer_anchor=(
                "A limiter measures requests against a policy and rejects or delays "
                "excess work while accounting for distribution and clock boundaries."
            ),
        ),
        ManualTopicIn(
            topic="Message queues",
            answer_anchor=(
                "A queue decouples producers and consumers; delivery semantics, "
                "ordering, retries, and idempotency define correctness."
            ),
        ),
        ManualTopicIn(
            topic="Database indexing",
            answer_anchor=(
                "An index maintains an ordered access path that trades write and "
                "storage cost for selective lookup performance."
            ),
        ),
    ],
)


async def _owned_source(
    db: AsyncSession, source_id: uuid.UUID, *, for_update: bool = False
) -> MaterialSource:
    statement = select(MaterialSource).where(
        MaterialSource.id == source_id,
        MaterialSource.user_id == current_user_id(),
    )
    if for_update:
        statement = statement.with_for_update()
    source = (await db.exec(statement)).first()
    if source is None:
        raise HTTPException(status_code=404, detail="material not found")
    return source


async def _active_pilot_enrollment(
    db: AsyncSession,
    *,
    user_id: uuid.UUID | None = None,
    for_update: bool = False,
) -> StudyPilotEnrollment | None:
    statement = select(StudyPilotEnrollment).where(
        StudyPilotEnrollment.user_id == (user_id or current_user_id()),
        StudyPilotEnrollment.withdrawn_at.is_(None),
    )
    if for_update:
        statement = statement.with_for_update().execution_options(populate_existing=True)
    return (await db.exec(statement)).first()


async def _require_pilot_compatible_build(
    request: Request, db: AsyncSession
) -> StudyPilotEnrollment | None:
    """Block legacy authority-bearing payloads only for active participants."""
    enrollment = await _active_pilot_enrollment(db)
    if enrollment is None:
        return None
    if not pilot_consent_is_valid(
        enrollment.consent_version,
        enrollment.consented_at,
    ):
        raise HTTPException(
            status_code=409,
            detail={"code": "pilot_consent_invalid"},
        )
    assignments = list(
        (
            await db.exec(
                select(StudyPilotAssignment).where(
                    StudyPilotAssignment.enrollment_id == enrollment.id
                )
            )
        ).all()
    )
    invalid_snapshot = any(
        not isinstance(assignment.version_snapshot, dict)
        for assignment in assignments
    )
    minimum_build_values = [
        assignment.version_snapshot.get("minimum_client_build")
        for assignment in assignments
        if isinstance(assignment.version_snapshot, dict)
    ]
    if assignments and (
        invalid_snapshot
        or any(
            not isinstance(value, int) or isinstance(value, bool)
            for value in minimum_build_values
        )
        or set(minimum_build_values) != {PILOT_MINIMUM_CLIENT_BUILD}
    ):
        raise HTTPException(
            status_code=409,
            detail={"code": "pilot_contract_mismatch"},
        )
    required_build = (
        minimum_build_values[0]
        if minimum_build_values
        else PILOT_MINIMUM_CLIENT_BUILD
    )
    raw_build = request.headers.get("X-Devmax-Client-Build", "")
    try:
        client_build = int(raw_build)
    except ValueError:
        client_build = 0
    if client_build < required_build:
        raise HTTPException(
            status_code=426,
            detail={
                "code": "pilot_upgrade_required",
                "minimum_client_build": required_build,
            },
        )
    return enrollment


async def _pilot_assignment(
    db: AsyncSession,
    enrollment: StudyPilotEnrollment,
    source: MaterialSource,
    *,
    for_update: bool = False,
) -> StudyPilotAssignment | None:
    statement = select(StudyPilotAssignment).where(
        StudyPilotAssignment.enrollment_id == enrollment.id,
        StudyPilotAssignment.source_lineage_id == source.lineage_id,
    )
    if for_update:
        statement = statement.with_for_update().execution_options(populate_existing=True)
    return (await db.exec(statement)).first()


async def _owned_proposal(
    db: AsyncSession,
    proposal_id: uuid.UUID,
    *,
    for_update: bool = False,
) -> tuple[MaterialTopicProposal, MaterialSource]:
    proposal = await db.get(MaterialTopicProposal, proposal_id)
    if proposal is None:
        raise HTTPException(status_code=404, detail="topic not found")
    source = await _owned_source(db, proposal.source_id, for_update=for_update)
    if for_update:
        proposal = await db.get(
            MaterialTopicProposal,
            proposal_id,
            with_for_update=True,
            populate_existing=True,
        )
        if proposal is None:
            raise HTTPException(status_code=404, detail="topic not found")
    return proposal, source


async def _owned_lesson_check(
    db: AsyncSession,
    check_id: uuid.UUID,
    *,
    for_update: bool = False,
) -> LessonCheck:
    statement = select(LessonCheck).where(
        LessonCheck.id == check_id,
        LessonCheck.user_id == current_user_id(),
    )
    if for_update:
        statement = statement.with_for_update().execution_options(populate_existing=True)
    check = (await db.exec(statement)).first()
    if check is None:
        raise HTTPException(status_code=404, detail="lesson check not found")
    return check


async def _assigned_pilot_target(
    db: AsyncSession,
    proposal: MaterialTopicProposal,
    source: MaterialSource,
    *,
    expected_condition: str | None = None,
    require_review: bool = True,
) -> tuple[StudyPilotEnrollment, StudyPilotAssignment]:
    enrollment = await _active_pilot_enrollment(db)
    if enrollment is None:
        raise HTTPException(status_code=404, detail={"code": "pilot_not_enrolled"})
    assignment = await _pilot_assignment(db, enrollment, source)
    if (
        assignment is None
        or assignment.source_id != source.id
        or assignment.target_proposal_id != proposal.id
        or assignment.bound_at is None
    ):
        raise HTTPException(
            status_code=409,
            detail={"code": "pilot_assignment_not_ready"},
        )
    if expected_condition is not None and assignment.condition != expected_condition:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "pilot_condition_mismatch",
                "assigned_condition": assignment.condition,
            },
        )
    if source.import_path != "lesson" or source.status not in {
        SOURCE_READY,
        SOURCE_NEEDS_ATTENTION,
        SOURCE_CONFIRMED,
        SOURCE_SUPERSEDED,
    }:
        raise HTTPException(status_code=409, detail={"code": "lesson_not_ready"})
    if proposal.status not in {PROPOSAL_CLEAN, PROPOSAL_CONFIRMED}:
        raise HTTPException(status_code=409, detail={"code": "proposal_not_available"})
    if source.result_summary.get("grounding_gate_version") != (
        materials.LESSON_GROUNDING_GATE_VERSION
    ):
        raise HTTPException(status_code=409, detail={"code": "lesson_grounding_required"})
    if require_review:
        audit = (
            await db.exec(
                select(LessonProposalAudit).where(
                    LessonProposalAudit.proposal_id == proposal.id,
                    LessonProposalAudit.source_id == source.id,
                )
            )
        ).first()
        if (
            audit is None
            or audit.reviewer_decision
            not in {PROPOSAL_AUDIT_APPROVED, PROPOSAL_AUDIT_CORRECTED}
            or audit.reviewed_at is None
            or audit.grounding_gate_version
            != str(materials.LESSON_GROUNDING_GATE_VERSION)
        ):
            raise HTTPException(
                status_code=409,
                detail={"code": "pilot_review_required"},
            )
    return enrollment, assignment


def _lesson_check_out(check: LessonCheck) -> LessonCheckOut:
    return LessonCheckOut(
        id=check.id,
        proposal_id=check.proposal_id,
        card_id=check.card_id,
        kind=check.kind,
        condition=check.condition or None,
        prompt_level=check.prompt_level,
        prompt_version=check.prompt_version,
        prompt_text=check.prompt_text_snapshot,
        status=check.status,
        draft_text=check.draft_text,
        qualitative_outcome=check.qualitative_outcome or None,
        has_feedback=bool(check.feedback.strip()),
        exposed_at=check.exposed_at,
        recall_not_before_at=check.recall_not_before_at,
        available_at=check.available_at,
        started_at=check.started_at,
        submitted_at=check.submitted_at,
        updated_at=check.updated_at,
    )


async def _preview_response(
    db: AsyncSession,
    source: MaterialSource,
    enrollment: StudyPilotEnrollment,
) -> MaterialImportPreviewOut:
    assignment = await _pilot_assignment(db, enrollment, source)
    assignment_ready = bool(
        assignment is not None
        and assignment.source_id == source.id
        and assignment.target_proposal_id is not None
        and assignment.bound_at is not None
    )
    topics = (
        await db.exec(
            select(MaterialTopicProposal)
            .where(MaterialTopicProposal.source_id == source.id)
            .order_by(MaterialTopicProposal.position)
        )
    ).all()
    checks = (
        await db.exec(
            select(LessonCheck).where(
                LessonCheck.user_id == current_user_id(),
                col(LessonCheck.proposal_id).in_([topic.id for topic in topics]),
            )
        )
    ).all() if topics else []
    checks_by_proposal_kind = {
        (check.proposal_id, check.kind): check for check in checks
    }
    preview_topics: list[MaterialTopicPreviewOut] = []
    for topic in topics:
        is_target = bool(
            assignment_ready
            and assignment is not None
            and assignment.target_proposal_id == topic.id
        )
        formation = checks_by_proposal_kind.get(
            (topic.id, LESSON_CHECK_FORMATION)
        )
        transfer = checks_by_proposal_kind.get((topic.id, LESSON_CHECK_TRANSFER))
        formation_state = formation.status if formation is not None else "not_started"
        transfer_state = "unavailable"
        if not is_target:
            formation_state = "unavailable"
        elif transfer is not None:
            try:
                await _require_transfer_eligibility(
                    db,
                    proposal=topic,
                    source=source,
                    transfer=transfer,
                    require_opened=False,
                )
            except HTTPException:
                transfer_state = "locked"
            else:
                if transfer.status == LESSON_CHECK_SUBMITTED:
                    transfer_state = (
                        "submitted" if _transfer_was_opened(transfer) else "locked"
                    )
                elif transfer.status == LESSON_CHECK_EXPOSED:
                    transfer_state = (
                        "debriefed" if _transfer_was_opened(transfer) else "locked"
                    )
                else:
                    transfer_state = "available"
        preview_topics.append(
            MaterialTopicPreviewOut(
                id=topic.id,
                position=topic.position,
                section_title=topic.section_title,
                topic=topic.topic,
                formation_question=(
                    topic.canonical_question
                    if is_target
                    and assignment is not None
                    and assignment.condition == LESSON_CONDITION_ATTEMPT_FIRST
                    else None
                ),
                status=topic.status,
                issue=topic.issue,
                formation_state=formation_state,
                transfer_state=transfer_state,
            )
        )
    return MaterialImportPreviewOut(
        id=source.id,
        title=source.title,
        kind=source.kind,
        source_url=source.source_url,
        content_provenance=source.content_provenance,
        status=source.status,
        import_path=source.import_path,
        intent=source.intent,
        clean_count=sum(topic.status == PROPOSAL_CLEAN for topic in topics),
        attention_count=sum(topic.status == PROPOSAL_NEEDS_ATTENTION for topic in topics),
        error=source.error,
        lesson_grounding_required=_lesson_grounding_required(source),
        proposals_ready_at=source.proposals_ready_at,
        review_opened_at=source.review_opened_at,
        confirmed_at=source.confirmed_at,
        topics=preview_topics,
    )


async def _advance_proposal_exposure(
    db: AsyncSession,
    proposal: MaterialTopicProposal,
    *,
    check: LessonCheck | None = None,
    record_initial_check_exposure: bool = False,
) -> tuple[datetime, datetime]:
    settings_row = await get_settings_row(db)
    card: Card | None = None
    if proposal.card_id is not None:
        owned_card = await db.get(
            Card,
            proposal.card_id,
            with_for_update=True,
            populate_existing=True,
        )
        if owned_card is not None and owned_card.user_id == current_user_id():
            card = owned_card
            live = (
                await db.exec(
                    select(Session.id).where(
                        Session.card_id == card.id,
                        col(Session.status).in_(LIVE_STATUSES),
                    )
                )
            ).first()
            if live is not None:
                raise HTTPException(
                    status_code=409,
                    detail={
                        "code": "live_session",
                        "message": (
                            "finish the in-progress answer before opening "
                            "learning material"
                        ),
                    },
                )
    local_now = now_in(settings_row.timezone)
    exposed_at, recall_not_before_at = learning_exposure_boundary(
        local_now,
        existing_recall_not_before_at=proposal.recall_not_before_at,
    )
    proposal.last_learning_exposure_at = exposed_at
    proposal.recall_not_before_at = recall_not_before_at
    proposal.updated_at = exposed_at
    db.add(proposal)
    if check is not None and record_initial_check_exposure:
        check.exposed_at = exposed_at
        check.recall_not_before_at = recall_not_before_at
        db.add(check)
    if card is not None:
        card.last_learning_exposure_at = exposed_at
        card.recall_not_before_at = recall_not_before_at
        db.add(card)
    return exposed_at, recall_not_before_at


def _authority_response(
    source: MaterialSource,
    proposal: MaterialTopicProposal,
    check: LessonCheck,
    *,
    feedback: str,
) -> MaterialTopicAuthorityOut:
    if (
        proposal.last_learning_exposure_at is None
        or proposal.recall_not_before_at is None
    ):
        raise RuntimeError("authority response built without a committed exposure boundary")
    return MaterialTopicAuthorityOut(
        check=_lesson_check_out(check),
        proposal_id=proposal.id,
        topic=proposal.topic,
        section_title=proposal.section_title,
        source_title=source.title,
        source_url=source.source_url,
        content_provenance=source.content_provenance,
        source_excerpt=proposal.source_excerpt,
        answer_basis=proposal.answer_anchor,
        canonical_question=proposal.canonical_question,
        answer_rubric=proposal.answer_rubric,
        recall_questions=proposal.recall_questions,
        feedback=feedback,
        exposed_at=proposal.last_learning_exposure_at,
        recall_not_before_at=proposal.recall_not_before_at,
        confirmation_title=AUTHORITY_CONFIRMATION_TITLE,
        confirmation_message=AUTHORITY_CONFIRMATION_MESSAGE,
    )


def _formation_provider_route(assignment: StudyPilotAssignment) -> dict[str, object]:
    snapshot = assignment.version_snapshot
    frozen = snapshot.get("formation_provider_route")
    if not isinstance(frozen, dict):
        raise HTTPException(status_code=409, detail={"code": "pilot_route_invalid"})
    route = dict(frozen)
    if route.get("provider") != "anthropic" or not str(route.get("model", "")).strip():
        raise HTTPException(status_code=409, detail={"code": "pilot_route_invalid"})
    return route


def _lesson_check_authorizer(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    check_id: uuid.UUID,
    provider_route: dict[str, object],
    operation_id: uuid.UUID,
):
    config = get_settings()

    async def require_live_check(boundary_db: AsyncSession) -> None:
        enrollment = await _active_pilot_enrollment(
            boundary_db, user_id=user_id, for_update=True
        )
        if enrollment is None:
            raise HTTPException(status_code=409, detail={"code": "pilot_withdrawn"})
        if not pilot_consent_is_valid(
            enrollment.consent_version,
            enrollment.consented_at,
        ):
            raise HTTPException(
                status_code=409,
                detail={"code": "pilot_consent_invalid"},
            )
        live = (
            await boundary_db.exec(
                select(LessonCheck)
                .where(
                    LessonCheck.id == check_id,
                    LessonCheck.user_id == user_id,
                    LessonCheck.kind == LESSON_CHECK_FORMATION,
                    LessonCheck.condition == LESSON_CONDITION_ATTEMPT_FIRST,
                    LessonCheck.status == LESSON_CHECK_OPEN,
                )
                .with_for_update()
            )
        ).first()
        if live is None:
            raise HTTPException(status_code=409, detail={"code": "lesson_check_not_open"})
        assignment = (
            await boundary_db.exec(
                select(StudyPilotAssignment)
                .join(
                    MaterialTopicProposal,
                    MaterialTopicProposal.id
                    == StudyPilotAssignment.target_proposal_id,
                )
                .join(
                    MaterialSource,
                    MaterialSource.id == MaterialTopicProposal.source_id,
                )
                .where(
                    StudyPilotAssignment.enrollment_id == enrollment.id,
                    StudyPilotAssignment.source_id == MaterialSource.id,
                    StudyPilotAssignment.source_lineage_id
                    == MaterialSource.lineage_id,
                    StudyPilotAssignment.bound_at.is_not(None),
                    StudyPilotAssignment.condition
                    == LESSON_CONDITION_ATTEMPT_FIRST,
                    MaterialTopicProposal.id == live.proposal_id,
                    MaterialTopicProposal.status == PROPOSAL_CLEAN,
                    MaterialSource.user_id == user_id,
                    MaterialSource.import_path == "lesson",
                    col(MaterialSource.status).in_(
                        (SOURCE_READY, SOURCE_NEEDS_ATTENTION)
                    ),
                )
                .with_for_update()
            )
        ).first()
        if (
            assignment is None
            or _formation_provider_route(assignment) != provider_route
            or assignment.version_snapshot.get("formation_prompt_version")
            != live.prompt_version
        ):
            raise HTTPException(
                status_code=409,
                detail={"code": "pilot_assignment_not_ready"},
            )

        authorization_details = (
            await boundary_db.exec(
                select(LLMUsage.details).where(
                    LLMUsage.user_id == user_id,
                    col(LLMUsage.operation).in_(
                        ("lesson_formation", "lesson_formation_retry")
                    ),
                )
            )
        ).all()
        terminal_details = (
            await boundary_db.exec(
                select(LLMUsage.details).where(
                    LLMUsage.user_id == user_id,
                    LLMUsage.operation
                    == usage.LESSON_FORMATION_TERMINAL_OPERATION,
                )
            )
        ).all()
        current_operation_id = str(operation_id)
        authorizations_by_operation: dict[str, int] = {}
        for details in authorization_details:
            if details.get("lesson_check_id") != str(check_id):
                continue
            reserved_operation_id = str(details.get("operation_id", ""))
            if not reserved_operation_id or reserved_operation_id == current_operation_id:
                continue
            authorizations_by_operation[reserved_operation_id] = (
                authorizations_by_operation.get(reserved_operation_id, 0) + 1
            )
        terminals_by_operation: dict[str, list[dict[str, object]]] = {}
        for details in terminal_details:
            if details.get("lesson_check_id") != str(check_id):
                continue
            reserved_operation_id = str(details.get("lesson_operation_id", ""))
            if reserved_operation_id:
                terminals_by_operation.setdefault(reserved_operation_id, []).append(
                    details
                )
        for reserved_operation_id, authorization_count in (
            authorizations_by_operation.items()
        ):
            terminals = terminals_by_operation.get(reserved_operation_id, [])
            if len(terminals) < authorization_count or any(
                terminal.get("product_outcome") == "pending"
                for terminal in terminals
            ):
                raise HTTPException(
                    status_code=409,
                    detail={
                        "code": "lesson_check_evaluation_indeterminate",
                        "retryable": False,
                    },
                )

    return usage.provider_call_authorizer(
        db,
        user_id,
        "lesson_formation",
        config=config,
        provider="anthropic",
        model=str(provider_route["model"]),
        boundary_check=require_live_check,
        operation_id=operation_id,
        audit_context={"lesson_check_id": str(check_id)},
    )


async def _proposal_check(
    db: AsyncSession,
    proposal_id: uuid.UUID,
    kind: str,
    *,
    for_update: bool = False,
) -> LessonCheck | None:
    statement = select(LessonCheck).where(
        LessonCheck.user_id == current_user_id(),
        LessonCheck.proposal_id == proposal_id,
        LessonCheck.kind == kind,
    )
    if for_update:
        statement = statement.with_for_update().execution_options(populate_existing=True)
    return (await db.exec(statement)).first()


def _transfer_route_time(check: LessonCheck, key: str) -> datetime | None:
    raw = check.provider_route.get(key)
    if not isinstance(raw, str) or not raw.strip():
        return None
    try:
        value = datetime.fromisoformat(raw)
    except ValueError:
        return None
    return as_utc(value) if value.tzinfo is not None else None


def _transfer_was_opened(check: LessonCheck) -> bool:
    return _transfer_route_time(check, TRANSFER_OPENED_AT_KEY) is not None


async def _require_transfer_eligibility(
    db: AsyncSession,
    *,
    proposal: MaterialTopicProposal,
    source: MaterialSource,
    transfer: LessonCheck,
    require_opened: bool,
    for_update: bool = False,
) -> tuple[StudyPilotAssignment, LessonCheck, Card, datetime]:
    """Apply the one server-owned transfer gate to every transfer surface."""
    _, assignment = await _assigned_pilot_target(db, proposal, source)
    if (
        transfer.user_id != current_user_id()
        or transfer.proposal_id != proposal.id
        or transfer.kind != LESSON_CHECK_TRANSFER
        or transfer.condition != assignment.condition
    ):
        raise HTTPException(status_code=409, detail={"code": "pilot_condition_mismatch"})
    if (
        transfer.prompt_level not in {"application", "failure_tradeoff"}
        or not transfer.prompt_text_snapshot.strip()
        or not transfer.source_candidate_id
        or transfer.prompt_version != TRANSFER_PROMPT_VERSION
        or transfer.prompt_rubric_version != TRANSFER_PROMPT_RUBRIC_VERSION
        or assignment.version_snapshot.get("transfer_prompt_version")
        != TRANSFER_PROMPT_VERSION
        or assignment.version_snapshot.get("transfer_prompt_rubric_version")
        != TRANSFER_PROMPT_RUBRIC_VERSION
        or not transfer.prompt_reviewer_id
        or transfer.prompt_approved_at is None
    ):
        raise HTTPException(
            status_code=409,
            detail={"code": "transfer_prompt_not_approved"},
        )
    formation = await _proposal_check(
        db,
        proposal.id,
        LESSON_CHECK_FORMATION,
        for_update=for_update,
    )
    if (
        formation is None
        or formation.status != LESSON_CHECK_EXPOSED
        or formation.exposed_at is None
        or formation.recall_not_before_at is None
        or proposal.card_id is None
    ):
        raise HTTPException(status_code=409, detail={"code": "first_recall_required"})
    card = await db.get(
        Card,
        proposal.card_id,
        with_for_update=for_update,
        populate_existing=for_update,
    )
    if (
        card is None
        or card.user_id != current_user_id()
        or card.last_learning_exposure_at is None
        or card.recall_not_before_at is None
        or (transfer.card_id is not None and transfer.card_id != card.id)
    ):
        raise HTTPException(status_code=409, detail={"code": "first_recall_required"})
    expected_available_at = as_utc(formation.exposed_at) + TRANSFER_DELAY
    if (
        transfer.available_at is not None
        and as_utc(transfer.available_at) != expected_available_at
    ):
        raise HTTPException(
            status_code=409,
            detail={"code": "transfer_timing_invalid"},
        )
    latest_recall_boundary = as_utc(card.recall_not_before_at)
    latest_learning_exposure = as_utc(card.last_learning_exposure_at)
    latest_own_debrief_boundary = _transfer_route_time(
        transfer, TRANSFER_DEBRIEF_BOUNDARY_KEY
    )
    latest_own_debrief_exposure = _transfer_route_time(
        transfer, TRANSFER_DEBRIEF_EXPOSURE_KEY
    )
    qualified_recall_boundary = _transfer_route_time(
        transfer, TRANSFER_QUALIFIED_RECALL_BOUNDARY_KEY
    )
    effective_recall_boundary = (
        qualified_recall_boundary
        if latest_own_debrief_boundary == latest_recall_boundary
        and latest_own_debrief_exposure == latest_learning_exposure
        and qualified_recall_boundary is not None
        else latest_recall_boundary
    )
    recalled = (
        await db.exec(
            select(Session.id).where(
                Session.card_id == card.id,
                Session.status == STATUS_COMPLETE,
                Session.practice == False,  # noqa: E712 - SQL expression
                Session.started_at >= effective_recall_boundary,
            )
        )
    ).first()
    if recalled is None:
        raise HTTPException(status_code=409, detail={"code": "first_recall_required"})
    now = datetime.now(UTC)
    if now < expected_available_at:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "transfer_not_available",
                "available_at": expected_available_at.isoformat(),
            },
        )
    if require_opened and not _transfer_was_opened(transfer):
        raise HTTPException(status_code=409, detail={"code": "transfer_not_opened"})
    if require_opened and transfer.available_at is None:
        raise HTTPException(status_code=409, detail={"code": "transfer_timing_invalid"})
    return assignment, formation, card, expected_available_at


async def _reveal_formation_authority(
    db: AsyncSession,
    check: LessonCheck,
    proposal: MaterialTopicProposal,
    source: MaterialSource,
    *,
    feedback: str | None = None,
) -> MaterialTopicAuthorityOut:
    await _advance_proposal_exposure(db, proposal)
    await db.commit()
    return _authority_response(
        source,
        proposal,
        check,
        feedback=feedback if feedback is not None else check.feedback,
    )


def _lesson_check_submit_lock(check_id: uuid.UUID) -> asyncio.Lock:
    """Serialize pilot submits in the deployed single-replica API process."""
    return _LESSON_CHECK_SUBMIT_LOCKS.setdefault(check_id, asyncio.Lock())


def _lesson_check_call_details(
    trace: ScoringTrace | None,
    check: LessonCheck,
    *,
    operation_id: uuid.UUID,
    product_outcome: str,
) -> list[dict[str, object]] | None:
    """Attach privacy-safe check metadata to physical-call terminal evidence."""
    if trace is None:
        return None
    return [
        {
            **detail,
            "lesson_check_id": str(check.id),
            "proposal_id": str(check.proposal_id),
            "kind": check.kind,
            "condition": check.condition,
            "prompt_version": check.prompt_version,
            "lesson_operation_id": str(operation_id),
            "product_outcome": product_outcome,
        }
        for detail in trace.usage_details()
    ]


async def _mark_lesson_check_terminal_committed(
    db: AsyncSession,
    operation_id: uuid.UUID,
) -> None:
    """Finalize crash-gap evidence in the same transaction as the result."""
    rows = (
        await db.exec(
            select(LLMUsage).where(
                LLMUsage.user_id == current_user_id(),
                LLMUsage.operation == usage.LESSON_FORMATION_TERMINAL_OPERATION,
            )
        )
    ).all()
    for row in rows:
        if row.details.get("lesson_operation_id") != str(operation_id):
            continue
        row.details = {**row.details, "product_outcome": "committed"}
        db.add(row)


def _artifacts_ready(source: MaterialSource) -> bool:
    return bool(
        source.distilled_at
        and source.canonical_note_markdown
        and source.recall_export_markdown
    )


def _legacy_lesson_preview_requires_grounding(source: MaterialSource) -> bool:
    return bool(
        source.import_path == "lesson"
        and source.status in {SOURCE_READY, SOURCE_NEEDS_ATTENTION}
        and source.result_summary.get("grounding_gate_version")
        != materials.LESSON_GROUNDING_GATE_VERSION
    )


def _lesson_grounding_required(source: MaterialSource) -> bool:
    """Whether a legacy preview needs its first successful current-gate pass."""
    if _legacy_lesson_preview_requires_grounding(source):
        return True
    return bool(
        source.import_path == "lesson"
        and source.status == SOURCE_FAILED
        and source.result_summary.get(LESSON_GROUNDING_RECOVERY_MARKER) is True
        and source.result_summary.get("grounding_gate_version")
        != materials.LESSON_GROUNDING_GATE_VERSION
    )


def _response_from_topics(
    source: MaterialSource,
    topics: list[MaterialTopicProposal],
    *,
    character_count: int | None = None,
    artifacts_ready: bool | None = None,
) -> MaterialImportOut:
    summary = source.result_summary
    return MaterialImportOut(
        id=source.id,
        title=source.title,
        kind=source.kind,
        source_url=source.source_url,
        content_provenance=source.content_provenance,
        version=source.version,
        status=source.status,
        import_path=source.import_path,
        intent=source.intent,
        original_filename=source.original_filename,
        character_count=character_count if character_count is not None else len(source.source_text),
        clean_count=sum(row.status == PROPOSAL_CLEAN for row in topics),
        attention_count=sum(row.status == PROPOSAL_NEEDS_ATTENTION for row in topics),
        error=source.error,
        plan_draft_id=source.plan_draft_id,
        comparison={key: int(value) for key, value in (summary.get("comparison") or {}).items()},
        topics=[MaterialTopicOut.model_validate(row) for row in topics],
        lesson_grounding_required=_lesson_grounding_required(source),
        artifacts_ready=(
            _artifacts_ready(source) if artifacts_ready is None else artifacts_ready
        ),
        distilled_at=source.distilled_at,
        created_at=source.created_at,
        updated_at=source.updated_at,
    )


async def _response(db: AsyncSession, source: MaterialSource) -> MaterialImportOut:
    topics = (
        await db.exec(
            select(MaterialTopicProposal)
            .where(MaterialTopicProposal.source_id == source.id)
            .order_by(MaterialTopicProposal.position)
        )
    ).all()
    return _response_from_topics(source, list(topics))


def _safe_import_status(
    source: MaterialSource,
    *,
    character_count: int | None = None,
    artifacts_ready: bool | None = None,
) -> MaterialImportOut:
    """Preserve the installed polling wire shape without serializing authority."""
    response = _response_from_topics(
        source,
        [],
        character_count=character_count,
        artifacts_ready=artifacts_ready,
    )
    response.clean_count = int(source.result_summary.get("clean_count", 0))
    response.attention_count = int(source.result_summary.get("attention_count", 0))
    return response


async def _is_assigned_experimental_source(
    db: AsyncSession,
    enrollment: StudyPilotEnrollment | None,
    source: MaterialSource,
) -> bool:
    return bool(
        enrollment is not None
        and await _pilot_assignment(db, enrollment, source) is not None
    )


@router.post("/imports", response_model=MaterialImportOut, status_code=202)
async def start_import(
    body: MaterialImportIn,
    background: BackgroundTasks,
    request: Request,
    db: AsyncSession = Depends(get_session),
) -> MaterialImportOut:
    enrollment = await _require_pilot_compatible_build(request, db)
    user_id = current_user_id()
    previous = None
    if body.previous_version_id:
        previous = await _owned_source(db, body.previous_version_id)
    await storage.reserve_material_source(
        db, user_id=user_id, characters=len(body.source_text)
    )
    source = MaterialSource(
        user_id=user_id,
        lineage_id=previous.lineage_id if previous else uuid.uuid4(),
        previous_version_id=previous.id if previous else None,
        version=previous.version + 1 if previous else 1,
        kind=body.kind,
        title=body.title,
        source_text=body.source_text,
        source_url=body.source_url,
        content_provenance=body.content_provenance,
        original_filename=body.original_filename,
        mime_type=body.mime_type,
        import_path=body.import_path,
        intent=body.intent,
        status=SOURCE_PENDING,
        requested_weeks=body.requested_weeks,
        weekly_capacity_minutes=body.weekly_capacity_minutes,
        mode=body.mode,
        deadline=body.deadline,
    )
    db.add(source)
    await db.commit()
    await db.refresh(source)
    background.add_task(materials.process_import, source.id)
    if await _is_assigned_experimental_source(db, enrollment, source):
        return _safe_import_status(source)
    return await _response(db, source)


@router.get("/imports", response_model=list[MaterialImportOut])
async def list_imports(
    request: Request, db: AsyncSession = Depends(get_session)
) -> list[MaterialImportOut]:
    enrollment = await _require_pilot_compatible_build(request, db)
    rows = (
        await db.exec(
            select(MaterialSource, func.length(MaterialSource.source_text))
            .options(
                defer(MaterialSource.source_text),
                defer(MaterialSource.canonical_note_markdown),
                defer(MaterialSource.recall_export_markdown),
            )
            .where(MaterialSource.user_id == current_user_id())
            .order_by(col(MaterialSource.updated_at).desc())
        )
    ).all()
    if not rows:
        return []
    topics = (
        await db.exec(
            select(MaterialTopicProposal)
            .where(col(MaterialTopicProposal.source_id).in_([source.id for source, _ in rows]))
            .order_by(MaterialTopicProposal.source_id, MaterialTopicProposal.position)
        )
    ).all()
    by_source: dict[uuid.UUID, list[MaterialTopicProposal]] = {}
    for topic in topics:
        by_source.setdefault(topic.source_id, []).append(topic)
    responses: list[MaterialImportOut] = []
    for source, character_count in rows:
        if await _is_assigned_experimental_source(db, enrollment, source):
            responses.append(
                _safe_import_status(
                    source,
                    character_count=character_count or 0,
                    artifacts_ready=source.distilled_at is not None,
                )
            )
        else:
            responses.append(
                _response_from_topics(
                    source,
                    by_source.get(source.id, []),
                    character_count=character_count or 0,
                    artifacts_ready=source.distilled_at is not None,
                )
            )
    return responses


@router.get("/imports/{source_id}", response_model=MaterialImportOut)
async def get_import(
    source_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_session),
) -> MaterialImportOut:
    enrollment = await _require_pilot_compatible_build(request, db)
    source = await _owned_source(db, source_id)
    if await _is_assigned_experimental_source(db, enrollment, source):
        return _safe_import_status(source)
    return await _response(db, source)


@router.get("/imports/{source_id}/preview", response_model=MaterialImportPreviewOut)
async def get_import_preview(
    source_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_session),
) -> MaterialImportPreviewOut:
    enrollment = await _require_pilot_compatible_build(request, db)
    if enrollment is None:
        raise HTTPException(status_code=404, detail={"code": "pilot_not_enrolled"})
    source = await _owned_source(db, source_id)
    if await _pilot_assignment(db, enrollment, source) is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "pilot_source_not_assigned"},
        )
    return await _preview_response(db, source, enrollment)


@router.post(
    "/imports/{source_id}/review-opened",
    response_model=MaterialImportPreviewOut,
)
async def mark_import_review_opened(
    source_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_session),
) -> MaterialImportPreviewOut:
    enrollment = await _require_pilot_compatible_build(request, db)
    if enrollment is None:
        raise HTTPException(status_code=404, detail={"code": "pilot_not_enrolled"})
    source = await _owned_source(db, source_id, for_update=True)
    if source.status not in {
        SOURCE_READY,
        SOURCE_NEEDS_ATTENTION,
        SOURCE_CONFIRMED,
        SOURCE_SUPERSEDED,
    }:
        raise HTTPException(status_code=409, detail={"code": "lesson_not_ready"})
    # Resolve assignment before recording the funnel transition. An unbound
    # source is not yet participant-visible review work.
    assignment = await _pilot_assignment(db, enrollment, source)
    if (
        assignment is None
        or assignment.source_id != source.id
        or assignment.target_proposal_id is None
        or assignment.bound_at is None
    ):
        raise HTTPException(
            status_code=409,
            detail={"code": "pilot_assignment_not_ready"},
        )
    if (
        source.review_opened_at is None
        and source.status in {SOURCE_READY, SOURCE_NEEDS_ATTENTION}
    ):
        opened_at = datetime.now(UTC)
        source.review_opened_at = opened_at
        source.updated_at = opened_at
        db.add(source)
        await db.commit()
    return await _preview_response(db, source, enrollment)


@router.post(
    "/imports/{source_id}/retry",
    response_model=MaterialImportOut,
    status_code=202,
)
async def retry_import(
    source_id: uuid.UUID,
    background: BackgroundTasks,
    request: Request,
    db: AsyncSession = Depends(get_session),
) -> MaterialImportOut:
    enrollment = await _require_pilot_compatible_build(request, db)
    source = await _owned_source(db, source_id, for_update=True)
    legacy_recovery = _legacy_lesson_preview_requires_grounding(source)
    if source.status != SOURCE_FAILED and not legacy_recovery:
        await db.rollback()
        raise HTTPException(
            status_code=409,
            detail="only a failed import or a pre-gate lesson can be retried",
        )
    if legacy_recovery:
        source.result_summary = {
            **source.result_summary,
            LESSON_GROUNDING_RECOVERY_MARKER: True,
        }
    source.status = SOURCE_PENDING
    source.processing_run_id = None
    source.processing_heartbeat_at = None
    source.error = ""
    source.updated_at = datetime.now(UTC)
    db.add(source)
    await db.commit()
    await db.refresh(source)
    background.add_task(materials.process_import, source.id)
    if await _is_assigned_experimental_source(db, enrollment, source):
        return _safe_import_status(source)
    return await _response(db, source)


@router.patch(
    "/topics/{proposal_id}",
    response_model=MaterialTopicOut | MaterialTopicPreviewOut,
)
async def edit_topic(
    proposal_id: uuid.UUID,
    body: MaterialTopicEdit,
    request: Request,
    db: AsyncSession = Depends(get_session),
) -> MaterialTopicOut | MaterialTopicPreviewOut:
    enrollment = await _require_pilot_compatible_build(request, db)
    row_ref = await db.get(MaterialTopicProposal, proposal_id)
    if row_ref is None:
        raise HTTPException(status_code=404, detail="topic not found")
    source = await _owned_source(db, row_ref.source_id, for_update=True)
    if source.status not in {SOURCE_READY, SOURCE_NEEDS_ATTENTION}:
        raise HTTPException(
            status_code=409,
            detail={"code": "material_not_editable"},
        )
    row = await db.get(
        MaterialTopicProposal,
        proposal_id,
        with_for_update=True,
        populate_existing=True,
    )
    if row is None:
        raise HTTPException(status_code=404, detail="topic not found")

    if enrollment is not None:
        assignment = await _pilot_assignment(db, enrollment, source)
        if (
            assignment is not None
            and assignment.source_id == source.id
            and assignment.target_proposal_id == row.id
        ):
            formation = await _proposal_check(
                db, row.id, LESSON_CHECK_FORMATION, for_update=True
            )
            would_change_meaning_or_decision = bool(
                body.action in {"exclude", "merge"}
                or (body.topic is not None and body.topic.strip() != row.topic)
                or (
                    body.answer_anchor is not None
                    and body.answer_anchor.strip() != row.answer_anchor
                )
            )
            if would_change_meaning_or_decision and (
                formation is None or formation.status != LESSON_CHECK_EXPOSED
            ):
                raise HTTPException(
                    status_code=409,
                    detail={"code": "pilot_condition_required"},
                )

    topic = body.topic.strip() if body.topic is not None else row.topic
    answer_anchor = (
        body.answer_anchor.strip()
        if body.answer_anchor is not None
        else row.answer_anchor
    )
    if not topic:
        raise HTTPException(status_code=422, detail="topic must not be blank")
    content_changed = topic != row.topic or answer_anchor != row.answer_anchor
    row.topic = topic
    row.answer_anchor = answer_anchor
    if body.action == "exclude":
        row.status = PROPOSAL_EXCLUDED
        row.merged_into_id = None
    elif body.action == "merge":
        if body.merge_into_id is None:
            raise HTTPException(status_code=422, detail="merge target required")
        target = (
            await db.exec(
                select(MaterialTopicProposal).where(
                    MaterialTopicProposal.id == body.merge_into_id,
                    MaterialTopicProposal.source_id == row.source_id,
                    MaterialTopicProposal.id != row.id,
                    MaterialTopicProposal.status == PROPOSAL_CLEAN,
                )
            )
        ).first()
        if target is None:
            raise HTTPException(status_code=404, detail="merge target not found")
        row.status = PROPOSAL_EXCLUDED
        row.merged_into_id = body.merge_into_id
    elif source.import_path == "lesson":
        row.merged_into_id = None
        if content_changed:
            row.status = PROPOSAL_NEEDS_ATTENTION
            row.issue = (
                "Edited lesson content requires a new source-grounding check."
            )
    else:
        row.merged_into_id = None
        row.status = PROPOSAL_CLEAN if row.answer_anchor else PROPOSAL_NEEDS_ATTENTION
        row.issue = "" if row.answer_anchor else "A good answer anchor is required."
    row.updated_at = datetime.now(UTC)
    db.add(row)
    await db.commit()
    if await _is_assigned_experimental_source(db, enrollment, source):
        preview = await _preview_response(db, source, enrollment)
        return next(topic for topic in preview.topics if topic.id == row.id)
    return MaterialTopicOut.model_validate(row)


@router.post(
    "/topics/{proposal_id}/formation-check",
    response_model=LessonCheckOut,
)
async def start_formation_check(
    proposal_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_session),
) -> LessonCheckOut:
    await _require_pilot_compatible_build(request, db)
    proposal, source = await _owned_proposal(db, proposal_id, for_update=True)
    _, assignment = await _assigned_pilot_target(
        db,
        proposal,
        source,
        expected_condition=LESSON_CONDITION_ATTEMPT_FIRST,
    )
    existing = await _proposal_check(
        db, proposal.id, LESSON_CHECK_FORMATION, for_update=True
    )
    if existing is not None:
        return _lesson_check_out(existing)
    if proposal.status == PROPOSAL_CONFIRMED:
        raise HTTPException(status_code=409, detail={"code": "formation_required"})
    audit = (
        await db.exec(
            select(LessonProposalAudit).where(
                LessonProposalAudit.proposal_id == proposal.id
            )
        )
    ).one()
    provider_route = _formation_provider_route(assignment)
    prompt_version_value = assignment.version_snapshot.get("formation_prompt_version")
    if not isinstance(prompt_version_value, str) or not prompt_version_value.strip():
        raise HTTPException(status_code=409, detail={"code": "pilot_route_invalid"})
    prompt_version = prompt_version_value.strip()
    if prompt_version != llm.LESSON_CHECK_PROMPT_VERSION:
        raise HTTPException(status_code=409, detail={"code": "pilot_route_invalid"})
    check = LessonCheck(
        user_id=current_user_id(),
        proposal_id=proposal.id,
        kind=LESSON_CHECK_FORMATION,
        condition=LESSON_CONDITION_ATTEMPT_FIRST,
        prompt_level=LESSON_PROMPT_CANONICAL,
        prompt_version=prompt_version,
        provider_route=provider_route,
        prompt_text_snapshot=proposal.canonical_question,
        prompt_rubric_version=prompt_version,
        prompt_reviewer_id=audit.reviewer_id or None,
        prompt_approved_at=audit.reviewed_at,
        status=LESSON_CHECK_OPEN,
    )
    db.add(check)
    await db.commit()
    return _lesson_check_out(check)


@router.patch(
    "/lesson-checks/{check_id}/draft",
    response_model=LessonCheckOut,
)
async def save_lesson_check_draft(
    check_id: uuid.UUID,
    body: LessonCheckDraftIn,
    request: Request,
    db: AsyncSession = Depends(get_session),
) -> LessonCheckOut:
    await _require_pilot_compatible_build(request, db)
    if await _active_pilot_enrollment(db) is None:
        raise HTTPException(status_code=404, detail={"code": "pilot_not_enrolled"})
    check = await _owned_lesson_check(db, check_id, for_update=True)
    if check.kind == LESSON_CHECK_TRANSFER:
        proposal, source = await _owned_proposal(
            db, check.proposal_id, for_update=True
        )
        await _require_transfer_eligibility(
            db,
            proposal=proposal,
            source=source,
            transfer=check,
            require_opened=True,
            for_update=True,
        )
    if check.status != LESSON_CHECK_OPEN:
        raise HTTPException(status_code=409, detail={"code": "lesson_check_closed"})
    if check.draft_text != body.draft_text:
        check.draft_text = body.draft_text
        check.updated_at = datetime.now(UTC)
        db.add(check)
        await db.commit()
    return _lesson_check_out(check)


@router.get(
    "/lesson-checks/{check_id}",
    response_model=LessonCheckOut,
)
async def get_lesson_check(
    check_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_session),
) -> LessonCheckOut:
    await _require_pilot_compatible_build(request, db)
    if await _active_pilot_enrollment(db) is None:
        raise HTTPException(status_code=404, detail={"code": "pilot_not_enrolled"})
    check = await _owned_lesson_check(db, check_id)
    if check.kind == LESSON_CHECK_TRANSFER:
        proposal, source = await _owned_proposal(db, check.proposal_id)
        await _require_transfer_eligibility(
            db,
            proposal=proposal,
            source=source,
            transfer=check,
            require_opened=True,
        )
    return _lesson_check_out(check)


@router.post(
    "/topics/{proposal_id}/restudy",
    response_model=MaterialTopicAuthorityOut,
)
async def restudy_topic(
    proposal_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_session),
) -> MaterialTopicAuthorityOut:
    await _require_pilot_compatible_build(request, db)
    proposal, source = await _owned_proposal(db, proposal_id, for_update=True)
    _, assignment = await _assigned_pilot_target(
        db,
        proposal,
        source,
        expected_condition=LESSON_CONDITION_RESTUDY,
    )
    check = await _proposal_check(
        db, proposal.id, LESSON_CHECK_FORMATION, for_update=True
    )
    if check is None:
        prompt_version_value = assignment.version_snapshot.get("restudy_prompt_version")
        if not isinstance(prompt_version_value, str) or not prompt_version_value.strip():
            raise HTTPException(status_code=409, detail={"code": "pilot_route_invalid"})
        prompt_version = prompt_version_value.strip()
        if prompt_version != RESTUDY_PROMPT_VERSION:
            raise HTTPException(status_code=409, detail={"code": "pilot_route_invalid"})
        check = LessonCheck(
            user_id=current_user_id(),
            proposal_id=proposal.id,
            kind=LESSON_CHECK_FORMATION,
            condition=LESSON_CONDITION_RESTUDY,
            prompt_level=LESSON_PROMPT_CANONICAL,
            prompt_version=prompt_version,
            provider_route={},
            # A restudy participant must not receive the canonical test cue
            # before the authority-bearing boundary below.
            prompt_text_snapshot="",
            prompt_rubric_version=prompt_version,
            status=LESSON_CHECK_OPEN,
        )
        db.add(check)
        await db.flush()
    if check.condition != LESSON_CONDITION_RESTUDY:
        raise HTTPException(status_code=409, detail={"code": "pilot_condition_mismatch"})
    if check.status == LESSON_CHECK_EXPOSED:
        return await _reveal_formation_authority(
            db,
            check,
            proposal,
            source,
            feedback="Review the source-backed account before deciding whether to keep it.",
        )
    exposed_at, _ = await _advance_proposal_exposure(
        db,
        proposal,
        check=check,
        record_initial_check_exposure=True,
    )
    check.status = LESSON_CHECK_EXPOSED
    check.submitted_at = exposed_at
    check.updated_at = exposed_at
    db.add(check)
    await db.commit()
    return _authority_response(
        source,
        proposal,
        check,
        feedback="Review the source-backed account before deciding whether to keep it.",
    )


@router.post(
    "/lesson-checks/{check_id}/submit",
    response_model=MaterialTopicAuthorityOut | LessonCheckOut,
)
async def submit_lesson_check(
    check_id: uuid.UUID,
    body: LessonCheckSubmitIn,
    request: Request,
    db: AsyncSession = Depends(get_session),
) -> MaterialTopicAuthorityOut | LessonCheckOut:
    async with _lesson_check_submit_lock(check_id):
        return await _submit_lesson_check(
            check_id=check_id,
            body=body,
            request=request,
            db=db,
        )


async def _submit_lesson_check(
    *,
    check_id: uuid.UUID,
    body: LessonCheckSubmitIn,
    request: Request,
    db: AsyncSession,
) -> MaterialTopicAuthorityOut | LessonCheckOut:
    await _require_pilot_compatible_build(request, db)
    check = await _owned_lesson_check(db, check_id, for_update=True)
    proposal, source = await _owned_proposal(db, check.proposal_id, for_update=True)
    await _assigned_pilot_target(db, proposal, source)

    if check.kind == LESSON_CHECK_TRANSFER:
        await _require_transfer_eligibility(
            db,
            proposal=proposal,
            source=source,
            transfer=check,
            require_opened=True,
            for_update=True,
        )
        if check.status in {LESSON_CHECK_SUBMITTED, LESSON_CHECK_EXPOSED}:
            if check.answer_text != body.answer_text:
                raise HTTPException(
                    status_code=409,
                    detail={"code": "lesson_check_already_submitted"},
                )
            return _lesson_check_out(check)
        if check.status != LESSON_CHECK_OPEN:
            raise HTTPException(status_code=409, detail={"code": "lesson_check_closed"})
        completed_at = datetime.now(UTC)
        check.draft_text = body.answer_text
        check.answer_text = body.answer_text
        check.status = LESSON_CHECK_SUBMITTED
        check.submitted_at = completed_at
        check.updated_at = completed_at
        db.add(check)
        # Transfer is blind: no correction, authority, exposure, Session, or Card
        # mutation accompanies this durable response write.
        await db.commit()
        return _lesson_check_out(check)

    if (
        check.kind != LESSON_CHECK_FORMATION
        or check.condition != LESSON_CONDITION_ATTEMPT_FIRST
    ):
        raise HTTPException(status_code=409, detail={"code": "lesson_check_invalid"})
    if check.status == LESSON_CHECK_EXPOSED:
        if check.answer_text != body.answer_text:
            raise HTTPException(
                status_code=409,
                detail={"code": "lesson_check_already_submitted"},
            )
        return await _reveal_formation_authority(db, check, proposal, source)
    if check.status != LESSON_CHECK_OPEN:
        raise HTTPException(status_code=409, detail={"code": "lesson_check_closed"})

    # Persist only the recoverable draft before the paid call. The provider
    # authorization callback commits its own audit boundary; no partial result,
    # authority, exposure, card, or scheduler mutation may be staged there.
    check.draft_text = body.answer_text
    check.updated_at = datetime.now(UTC)
    db.add(check)
    await db.commit()

    provider_route = dict(check.provider_route)
    model = str(provider_route.get("model", "")).strip()
    effort_value = provider_route.get("effort")
    effort = str(effort_value) if effort_value is not None else None
    if provider_route.get("provider") != "anthropic" or not model:
        raise HTTPException(status_code=409, detail={"code": "pilot_route_invalid"})
    operation_id = uuid.uuid4()
    try:
        result = await llm.evaluate_lesson_check(
            model=model,
            effort=effort,
            topic=proposal.topic,
            question=check.prompt_text_snapshot,
            answer=body.answer_text,
            source_excerpt=proposal.source_excerpt,
            answer_basis=proposal.answer_anchor,
            answer_rubric=proposal.answer_rubric,
            before_provider_call=_lesson_check_authorizer(
                db,
                user_id=current_user_id(),
                check_id=check.id,
                provider_route=provider_route,
                operation_id=operation_id,
            ),
        )
    except llm.LLMError as exc:
        details = _lesson_check_call_details(
            exc.trace,
            check,
            operation_id=operation_id,
            product_outcome="failed",
        )
        if details:
            independently_committed = await usage.record_physical_calls(
                db,
                current_user_id(),
                usage.LESSON_FORMATION_TERMINAL_OPERATION,
                call_details=details,
            )
            if not independently_committed:
                await db.commit()
        raise

    call_details = _lesson_check_call_details(
        result.trace,
        check,
        operation_id=operation_id,
        product_outcome="pending",
    )
    if not call_details:
        raise llm.LLMError("lesson formation call audit unavailable")
    independently_committed = await usage.record_physical_calls(
        db,
        current_user_id(),
        usage.LESSON_FORMATION_TERMINAL_OPERATION,
        call_details=call_details,
    )
    if not independently_committed:
        await db.commit()

    if not await usage.lock_account_for_provider_result(db, current_user_id()):
        raise HTTPException(status_code=404, detail="lesson check not found")
    check = await _owned_lesson_check(db, check.id, for_update=True)
    proposal, source = await _owned_proposal(db, check.proposal_id, for_update=True)
    await _assigned_pilot_target(
        db,
        proposal,
        source,
        expected_condition=LESSON_CONDITION_ATTEMPT_FIRST,
    )
    if check.status == LESSON_CHECK_EXPOSED:
        return await _reveal_formation_authority(db, check, proposal, source)
    if check.status != LESSON_CHECK_OPEN:
        raise HTTPException(status_code=409, detail={"code": "lesson_check_closed"})
    exposed_at, _ = await _advance_proposal_exposure(
        db,
        proposal,
        check=check,
        record_initial_check_exposure=True,
    )
    check.answer_text = body.answer_text
    check.qualitative_outcome = result.qualitative_outcome
    check.feedback = result.feedback
    check.status = LESSON_CHECK_EXPOSED
    check.submitted_at = exposed_at
    check.updated_at = exposed_at
    db.add(check)
    await _mark_lesson_check_terminal_committed(db, operation_id)
    await db.commit()
    return _authority_response(source, proposal, check, feedback=check.feedback)


@router.post(
    "/lesson-checks/{check_id}/authority",
    response_model=MaterialTopicAuthorityOut,
)
async def replay_lesson_check_authority(
    check_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_session),
) -> MaterialTopicAuthorityOut:
    await _require_pilot_compatible_build(request, db)
    check = await _owned_lesson_check(db, check_id, for_update=True)
    if check.kind != LESSON_CHECK_FORMATION or check.status != LESSON_CHECK_EXPOSED:
        raise HTTPException(status_code=409, detail={"code": "authority_unavailable"})
    proposal, source = await _owned_proposal(db, check.proposal_id, for_update=True)
    await _assigned_pilot_target(db, proposal, source)
    return await _reveal_formation_authority(db, check, proposal, source)


@router.post(
    "/topics/{proposal_id}/transfer-check",
    response_model=LessonCheckOut,
)
async def start_transfer_check(
    proposal_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_session),
) -> LessonCheckOut:
    await _require_pilot_compatible_build(request, db)
    proposal, source = await _owned_proposal(db, proposal_id, for_update=True)
    transfer = await _proposal_check(
        db, proposal.id, LESSON_CHECK_TRANSFER, for_update=True
    )
    if transfer is None:
        raise HTTPException(
            status_code=409,
            detail={"code": "transfer_prompt_not_ready"},
        )
    _, _, card, expected_available_at = await _require_transfer_eligibility(
        db,
        proposal=proposal,
        source=source,
        transfer=transfer,
        require_opened=False,
        for_update=True,
    )
    now = datetime.now(UTC)
    transfer.available_at = expected_available_at
    transfer.card_id = card.id
    if not _transfer_was_opened(transfer):
        transfer.provider_route = {
            **transfer.provider_route,
            TRANSFER_OPENED_AT_KEY: now.isoformat(),
        }
        transfer.started_at = now
    transfer.updated_at = now
    db.add(transfer)
    await db.commit()
    return _lesson_check_out(transfer)


@router.post(
    "/lesson-checks/{check_id}/transfer-debrief",
    response_model=MaterialTopicAuthorityOut,
)
async def transfer_debrief(
    check_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_session),
) -> MaterialTopicAuthorityOut:
    await _require_pilot_compatible_build(request, db)
    check = await _owned_lesson_check(db, check_id, for_update=True)
    if check.kind != LESSON_CHECK_TRANSFER:
        raise HTTPException(status_code=409, detail={"code": "transfer_debrief_unavailable"})
    proposal, source = await _owned_proposal(db, check.proposal_id, for_update=True)
    _, _, card, _ = await _require_transfer_eligibility(
        db,
        proposal=proposal,
        source=source,
        transfer=check,
        require_opened=True,
        for_update=True,
    )
    if check.status not in {
        LESSON_CHECK_SUBMITTED,
        LESSON_CHECK_EXPOSED,
    }:
        raise HTTPException(status_code=409, detail={"code": "transfer_debrief_unavailable"})
    first_debrief = check.status == LESSON_CHECK_SUBMITTED
    current_card_boundary = as_utc(card.recall_not_before_at)
    current_card_exposure = as_utc(card.last_learning_exposure_at)
    latest_own_debrief_boundary = _transfer_route_time(
        check, TRANSFER_DEBRIEF_BOUNDARY_KEY
    )
    latest_own_debrief_exposure = _transfer_route_time(
        check, TRANSFER_DEBRIEF_EXPOSURE_KEY
    )
    qualified_boundary = (
        _transfer_route_time(check, TRANSFER_QUALIFIED_RECALL_BOUNDARY_KEY)
        if latest_own_debrief_boundary == current_card_boundary
        and latest_own_debrief_exposure == current_card_exposure
        else current_card_boundary
    )
    exposed_at, recall_not_before_at = await _advance_proposal_exposure(
        db,
        proposal,
        check=check,
        record_initial_check_exposure=first_debrief,
    )
    if first_debrief:
        check.status = LESSON_CHECK_EXPOSED
        check.updated_at = exposed_at
    route = {
        **check.provider_route,
        TRANSFER_DEBRIEF_EXPOSURE_KEY: exposed_at.isoformat(),
        TRANSFER_DEBRIEF_BOUNDARY_KEY: recall_not_before_at.isoformat(),
    }
    if qualified_boundary is not None:
        route[TRANSFER_QUALIFIED_RECALL_BOUNDARY_KEY] = (
            qualified_boundary.isoformat()
        )
    check.provider_route = route
    db.add(check)
    await db.commit()
    return _authority_response(
        source,
        proposal,
        check,
        feedback="Compare your response with the source-backed account below.",
    )


@router.post("/imports/{source_id}/confirm", response_model=MaterialConfirmOut)
async def confirm_topics(
    source_id: uuid.UUID,
    body: MaterialConfirmIn,
    request: Request,
    db: AsyncSession = Depends(get_session),
) -> MaterialConfirmOut:
    enrollment = await _require_pilot_compatible_build(request, db)
    source = await _owned_source(db, source_id, for_update=True)
    assignment = (
        await _pilot_assignment(db, enrollment, source, for_update=True)
        if enrollment is not None
        else None
    )
    pilot_assignment = (
        assignment
        if assignment is not None and assignment.source_id == source.id
        else None
    )
    if assignment is not None and pilot_assignment is None:
        raise HTTPException(status_code=409, detail={"code": "pilot_assignment_not_ready"})

    if source.status == SOURCE_CONFIRMED and pilot_assignment is not None:
        target = await db.get(MaterialTopicProposal, pilot_assignment.target_proposal_id)
        if target is None or target.source_id != source.id:
            raise HTTPException(status_code=409, detail={"code": "pilot_assignment_not_ready"})
        selected = set(body.selected_topic_ids)
        expected = {target.id} if target.status == PROPOSAL_CONFIRMED else set()
        if selected != expected:
            raise HTTPException(status_code=409, detail={"code": "confirmation_mismatch"})
        return MaterialConfirmOut(
            source_id=source.id,
            created_card_ids=[target.card_id] if target.card_id is not None else [],
        )
    if source.status not in {SOURCE_READY, SOURCE_NEEDS_ATTENTION}:
        raise HTTPException(
            status_code=409,
            detail={"code": "material_not_confirmable"},
        )
    if pilot_assignment is None and not body.selected_topic_ids:
        raise HTTPException(
            status_code=422,
            detail={"code": "at_least_one_topic_required"},
        )
    if source.import_path == "lesson":
        if source.result_summary.get("grounding_gate_version") != (
            materials.LESSON_GROUNDING_GATE_VERSION
        ):
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "lesson_grounding_required",
                    "message": "Process this lesson again before confirming concepts.",
                },
            )
        classification = body.content_provenance or source.content_provenance
        if classification == CONTENT_PROVENANCE_LEGACY_UNSPECIFIED:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "content_provenance_required",
                    "message": "Choose what the lesson text represents before confirming.",
                },
            )
        source.content_provenance = classification
        db.add(source)

    pilot_target: MaterialTopicProposal | None = None
    pilot_formation: LessonCheck | None = None
    if pilot_assignment is not None:
        if source.review_opened_at is None:
            raise HTTPException(status_code=409, detail={"code": "review_not_opened"})
        if (
            pilot_assignment.target_proposal_id is None
            or pilot_assignment.bound_at is None
        ):
            raise HTTPException(status_code=409, detail={"code": "pilot_assignment_not_ready"})
        pilot_target = await db.get(
            MaterialTopicProposal,
            pilot_assignment.target_proposal_id,
            with_for_update=True,
            populate_existing=True,
        )
        if pilot_target is None or pilot_target.source_id != source.id:
            raise HTTPException(status_code=409, detail={"code": "pilot_assignment_not_ready"})
        selected = set(body.selected_topic_ids)
        if selected not in (set(), {pilot_target.id}):
            raise HTTPException(status_code=409, detail={"code": "pilot_target_only"})
        pilot_formation = await _proposal_check(
            db,
            pilot_target.id,
            LESSON_CHECK_FORMATION,
            for_update=True,
        )
        if (
            pilot_formation is None
            or pilot_formation.status != LESSON_CHECK_EXPOSED
            or pilot_target.last_learning_exposure_at is None
            or pilot_target.recall_not_before_at is None
        ):
            raise HTTPException(status_code=409, detail={"code": "pilot_condition_required"})
        if not selected:
            # Zero kept concepts is a real, explicit post-authority decision. It
            # closes the source funnel without manufacturing a Card.
            pilot_target.status = PROPOSAL_EXCLUDED
            pilot_target.updated_at = datetime.now(UTC)
            db.add(pilot_target)

    rows = (
        await db.exec(
            select(MaterialTopicProposal).where(
                MaterialTopicProposal.source_id == source.id,
                col(MaterialTopicProposal.id).in_(body.selected_topic_ids),
            )
        )
    ).all()
    if len(rows) != len(set(body.selected_topic_ids)):
        raise HTTPException(status_code=404, detail="topic not found")
    if any(row.status != PROPOSAL_CLEAN or not row.answer_anchor.strip() for row in rows):
        raise HTTPException(status_code=409, detail="topics still need attention")
    if source.import_path == "lesson":
        unselected_clean = (
            await db.exec(
                select(MaterialTopicProposal)
                .where(
                    MaterialTopicProposal.source_id == source.id,
                    MaterialTopicProposal.status == PROPOSAL_CLEAN,
                    col(MaterialTopicProposal.id).not_in(body.selected_topic_ids),
                )
                .order_by(MaterialTopicProposal.position)
            )
        ).all()
        needs_attention = (
            await db.exec(
                select(MaterialTopicProposal)
                .where(
                    MaterialTopicProposal.source_id == source.id,
                    MaterialTopicProposal.status == PROPOSAL_NEEDS_ATTENTION,
                )
                .order_by(MaterialTopicProposal.position)
            )
        ).all()
        if unselected_clean or needs_attention:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "lesson_decisions_incomplete",
                    "unselected_clean_topic_ids": [
                        str(row.id) for row in unselected_clean
                    ],
                    "needs_attention_topic_ids": [
                        str(row.id) for row in needs_attention
                    ],
                },
            )
    user_id = current_user_id()
    await lock_topic_creation(db, user_id)
    existing = await study_plan.normalized_card_index(db, user_id)
    normalized = [study_plan.normalize_topic(row.topic) for row in rows]
    if len(set(normalized)) != len(normalized) or any(key in existing for key in normalized):
        raise HTTPException(status_code=409, detail="duplicate topic")
    today = await local_today(db)
    cards: list[Card] = []
    for row in rows:
        if source.import_path == "lesson":
            try:
                card = build_grounded_card(
                    user_id=current_user_id(),
                    topic=row.topic,
                    category=row.section_title or source.title,
                    grounding=Grounding(
                        source_url=source.source_url,
                        source_section=row.section_title,
                        source_label=source.title,
                        answer_basis=row.answer_anchor,
                        answer_rubric=row.answer_rubric,
                        canonical_question=row.canonical_question,
                    ),
                    today=today,
                    schedule="now",
                )
            except GroundingError as exc:
                raise HTTPException(
                    status_code=409,
                    detail={"code": "missing_grounding", "missing": exc.missing},
                ) from exc
            # Compatibility fields keep the existing learning/scoring fallback
            # available while the full grounding fields are the authority.
            card.answer_anchor = row.answer_anchor
            card.source_excerpt = row.source_excerpt
            card.source_id = source.id
            if pilot_assignment is not None:
                if (
                    row.last_learning_exposure_at is None
                    or row.recall_not_before_at is None
                ):
                    raise HTTPException(
                        status_code=409,
                        detail={"code": "pilot_condition_required"},
                    )
                card.last_learning_exposure_at = row.last_learning_exposure_at
                card.recall_not_before_at = row.recall_not_before_at
        else:
            card = Card(
                user_id=current_user_id(),
                topic=row.topic,
                category=row.section_title or "Imported guide",
                delivery_mode=DELIVERY_CONVERSATIONAL,
                next_review_at=today,
                answer_anchor=row.answer_anchor,
                source_excerpt=row.source_excerpt,
                source_id=source.id,
            )
        cards.append(card)
    for card, row in zip(cards, rows, strict=True):
        db.add(card)
        row.card_id = card.id
        row.status = PROPOSAL_CONFIRMED
        db.add(row)
        checks = (
            await db.exec(
                select(LessonCheck).where(
                    LessonCheck.user_id == current_user_id(),
                    LessonCheck.proposal_id == row.id,
                )
            )
        ).all()
        for check in checks:
            check.card_id = card.id
            db.add(check)
    unselected = (
        await db.exec(
            select(MaterialTopicProposal).where(
                MaterialTopicProposal.source_id == source.id,
                col(MaterialTopicProposal.status).in_((PROPOSAL_CLEAN, PROPOSAL_NEEDS_ATTENTION)),
                col(MaterialTopicProposal.id).not_in(body.selected_topic_ids),
            )
        )
    ).all()
    for row in unselected:
        row.status = PROPOSAL_EXCLUDED
        db.add(row)
    await materials.confirm_source_version(db, source, current_user_id())
    await db.commit()
    return MaterialConfirmOut(source_id=source.id, created_card_ids=[card.id for card in cards])


async def _lesson_graph(
    db: AsyncSession, source: MaterialSource
) -> tuple[
    list[tuple[MaterialTopicProposal, Card]],
    dict[uuid.UUID, list[Session]],
]:
    if source.import_path != "lesson":
        raise HTTPException(status_code=409, detail={"code": "not_a_lesson"})
    if source.status not in {SOURCE_CONFIRMED, SOURCE_SUPERSEDED}:
        raise HTTPException(
            status_code=409,
            detail={"code": "lesson_not_confirmed"},
        )
    proposals = (
        await db.exec(
            select(MaterialTopicProposal)
            .where(
                MaterialTopicProposal.source_id == source.id,
                MaterialTopicProposal.status == PROPOSAL_CONFIRMED,
            )
            .order_by(MaterialTopicProposal.position)
        )
    ).all()
    if not proposals or any(row.card_id is None for row in proposals):
        raise HTTPException(
            status_code=409,
            detail={"code": "lesson_has_no_confirmed_cards"},
        )
    card_ids = [row.card_id for row in proposals if row.card_id is not None]
    cards = (
        await db.exec(
            select(Card).where(
                Card.user_id == current_user_id(),
                col(Card.id).in_(card_ids),
            )
        )
    ).all()
    by_id = {card.id: card for card in cards}
    if any(row.card_id not in by_id for row in proposals):
        raise HTTPException(
            status_code=409,
            detail={"code": "lesson_card_missing"},
        )
    sessions = (
        await db.exec(
            select(Session)
            .where(
                col(Session.card_id).in_(card_ids),
                Session.status == STATUS_COMPLETE,
                Session.practice == False,  # noqa: E712 - SQL expression
            )
            .order_by(Session.started_at)
        )
    ).all()
    sessions_by_card: dict[uuid.UUID, list[Session]] = {}
    for session in sessions:
        card = by_id.get(session.card_id)
        if (
            card is not None
            and card.recall_not_before_at is not None
            and as_utc(session.started_at) < as_utc(card.recall_not_before_at)
        ):
            # Formation, practice, or a pre-boundary synthetic row is not the
            # delayed evidence that makes a concept distillable.
            continue
        sessions_by_card.setdefault(session.card_id, []).append(session)
    return [(row, by_id[row.card_id]) for row in proposals], sessions_by_card


def _learning_note_concepts(
    source: MaterialSource,
    graph: list[tuple[MaterialTopicProposal, Card]],
    sessions_by_card: dict[uuid.UUID, list[Session]],
) -> list[LearningNoteConcept]:
    concepts: list[LearningNoteConcept] = []
    for proposal, card in graph:
        rubric = proposal.answer_rubric
        gotchas = [
            f"Alternative: {rubric.get('acceptable_alternative', '').strip()}",
            f"Trade-off: {rubric.get('trade_off', '').strip()}",
            f"Failure mode: {rubric.get('failure_mode', '').strip()}",
            f"Misconception: {rubric.get('misconception', '').strip()}",
        ]
        gotchas = [value for value in gotchas if value.rsplit(":", 1)[-1].strip()]
        quiz_results = [
            LessonQuizResult(
                session_id=session.id,
                reviewed_at=session.ended_at or session.started_at,
                question=session.question_asked[:2000],
                recall_score=session.accuracy,
                scoring_contract_version=session.scoring_contract_version,
                scored_follow_up_used=session.follow_up_used,
                graded_summary=(session.feedback or card.mastery_summary)[:4000],
                feedback=session.feedback[:4000],
            )
            # The local writer intentionally caps durable quiz history at 20
            # rows per concept. Keep the newest unaided evidence at that boundary.
            for session in sessions_by_card.get(card.id, [])[-20:]
        ]
        latest_recall = quiz_results[-1].recall_score if quiz_results else None
        concepts.append(
            LearningNoteConcept(
                proposal_id=proposal.id,
                card_id=card.id,
                concept=proposal.topic,
                canonical_question=proposal.canonical_question,
                answer_rubric=proposal.answer_rubric,
                source_title=source.title,
                source_url=source.source_url,
                mental_model=proposal.answer_anchor,
                how_it_works=(
                    rubric.get("mechanism", "").strip() or proposal.answer_anchor
                ),
                gotchas=gotchas,
                recall_prompts=proposal.recall_questions,
                quiz_results=quiz_results,
                confidence=materials.confidence_for(latest_recall),
            )
        )
    return concepts


def _artifacts_response(
    source: MaterialSource, concepts: list[LearningNoteConcept]
) -> MaterialArtifactsOut:
    if not _artifacts_ready(source):
        raise HTTPException(
            status_code=409,
            detail={"code": "artifacts_not_ready"},
        )
    assert source.distilled_at is not None
    writeback_bundle = second_brain.build_learning_writeback_bundle(
        source_id=source.id,
        source_lineage_id=source.lineage_id,
        source_version=source.version,
        source_title=source.title,
        source_url=source.source_url,
        source_distilled_at=source.distilled_at,
        concepts=[concept.model_dump(mode="json") for concept in concepts],
    )
    return MaterialArtifactsOut(
        source_id=source.id,
        title=source.title,
        source_url=source.source_url,
        content_provenance=source.content_provenance,
        distilled_at=source.distilled_at,
        canonical_note_markdown=source.canonical_note_markdown,
        recall_export_markdown=source.recall_export_markdown,
        concepts=concepts,
        writeback_bundle=writeback_bundle,
    )


@router.get("/imports/{source_id}/progress", response_model=LessonProgressOut)
async def lesson_progress(
    source_id: uuid.UUID, db: AsyncSession = Depends(get_session)
) -> LessonProgressOut:
    source = await _owned_source(db, source_id)
    graph, sessions_by_card = await _lesson_graph(db, source)
    concepts: list[LessonConceptProgress] = []
    weak_count = 0
    next_card_id: uuid.UUID | None = None
    for proposal, card in graph:
        projection = project_card_score(card)
        reviewed = bool(sessions_by_card.get(card.id))
        if not reviewed and next_card_id is None:
            next_card_id = card.id
        if projection.recall_score is not None and projection.recall_score <= 2:
            weak_count += 1
        concepts.append(
            LessonConceptProgress(
                proposal_id=proposal.id,
                card_id=card.id,
                concept=proposal.topic,
                mastery_summary=card.mastery_summary,
                last_score=card.last_score,
                recall_score=projection.recall_score,
                score_kind=projection.score_kind,
                scoring_contract_version=projection.scoring_contract_version,
                last_reviewed_at=card.last_reviewed_at,
                next_review_at=card.next_review_at,
                interval_days=card.interval_days,
            )
        )
    reviewed_count = sum(bool(sessions_by_card.get(card.id)) for _, card in graph)
    return LessonProgressOut(
        source_id=source.id,
        title=source.title,
        concept_count=len(graph),
        reviewed_count=reviewed_count,
        weak_count=weak_count,
        complete=reviewed_count == len(graph),
        next_card_id=next_card_id,
        concepts=concepts,
    )


@router.post("/imports/{source_id}/distill", response_model=MaterialArtifactsOut)
async def distill_lesson(
    source_id: uuid.UUID, db: AsyncSession = Depends(get_session)
) -> MaterialArtifactsOut:
    source = await _owned_source(db, source_id)
    graph, sessions_by_card = await _lesson_graph(db, source)
    reviewed_count = sum(bool(sessions_by_card.get(card.id)) for _, card in graph)
    if reviewed_count != len(graph):
        raise HTTPException(
            status_code=409,
            detail={
                "code": "lesson_incomplete",
                "reviewed_count": reviewed_count,
                "concept_count": len(graph),
            },
        )
    concepts = _learning_note_concepts(source, graph, sessions_by_card)
    note, recall = materials.render_lesson_markdown(
        source, [concept.model_dump(mode="python") for concept in concepts]
    )
    if (
        source.canonical_note_markdown != note
        or source.recall_export_markdown != recall
        or source.distilled_at is None
    ):
        source.canonical_note_markdown = note
        source.recall_export_markdown = recall
        source.distilled_at = datetime.now(UTC)
        source.updated_at = source.distilled_at
        db.add(source)
        await db.commit()
    return _artifacts_response(source, concepts)


@router.get("/imports/{source_id}/artifacts", response_model=MaterialArtifactsOut)
async def lesson_artifacts(
    source_id: uuid.UUID, db: AsyncSession = Depends(get_session)
) -> MaterialArtifactsOut:
    source = await _owned_source(db, source_id)
    if not _artifacts_ready(source):
        raise HTTPException(
            status_code=409,
            detail={"code": "artifacts_not_ready"},
        )
    graph, sessions_by_card = await _lesson_graph(db, source)
    return _artifacts_response(
        source, _learning_note_concepts(source, graph, sessions_by_card)
    )


async def _create_manual(
    db: AsyncSession, title: str, topics: list[ManualTopicIn], kind: str
) -> MaterialConfirmOut:
    user_id = current_user_id()
    source_text = "\n\n".join(
        f"{item.topic}\n{item.answer_anchor}" for item in topics
    )
    await storage.reserve_material_source(
        db, user_id=user_id, characters=len(source_text)
    )
    source = MaterialSource(
        user_id=user_id,
        kind=kind,
        title=title,
        source_text=source_text,
        status=SOURCE_CONFIRMED,
    )
    db.add(source)
    await db.flush()
    today = await local_today(db)
    await lock_topic_creation(db, user_id)
    existing = await study_plan.normalized_card_index(db, user_id)
    keys = [study_plan.normalize_topic(item.topic) for item in topics]
    if len(keys) != len(set(keys)) or any(key in existing for key in keys):
        raise HTTPException(status_code=409, detail="duplicate topic")
    cards = []
    for position, item in enumerate(topics, 1):
        proposal = MaterialTopicProposal(
            source_id=source.id,
            position=position,
            topic=item.topic,
            answer_anchor=item.answer_anchor,
            status=PROPOSAL_CONFIRMED,
        )
        card = Card(
            user_id=user_id,
            topic=proposal.topic,
            category="Devmax collection" if kind == "collection" else "Manual topic",
            delivery_mode=DELIVERY_CONVERSATIONAL,
            next_review_at=today,
            answer_anchor=proposal.answer_anchor,
            source_id=source.id,
        )
        proposal.card_id = card.id
        db.add(proposal)
        db.add(card)
        cards.append(card)
    await db.commit()
    return MaterialConfirmOut(source_id=source.id, created_card_ids=[card.id for card in cards])


@router.post("/manual", response_model=MaterialConfirmOut, status_code=201)
async def create_manual(
    body: ManualMaterialIn, db: AsyncSession = Depends(get_session)
) -> MaterialConfirmOut:
    return await _create_manual(db, body.title, body.topics, "manual")


@router.get("/collections", response_model=list[CollectionSummary])
async def collections() -> list[CollectionSummary]:
    return [
        CollectionSummary(**COLLECTION.model_dump(exclude={"sections", "source_note", "topics"}))
    ]


@router.get("/collections/{collection_id}", response_model=CollectionDetail)
async def collection(collection_id: str) -> CollectionDetail:
    if collection_id != COLLECTION.id:
        raise HTTPException(status_code=404, detail="collection not found")
    return COLLECTION


@router.post("/collections/{collection_id}", response_model=MaterialConfirmOut, status_code=201)
async def add_collection(
    collection_id: str, db: AsyncSession = Depends(get_session)
) -> MaterialConfirmOut:
    if collection_id != COLLECTION.id:
        raise HTTPException(status_code=404, detail="collection not found")
    return await _create_manual(db, COLLECTION.title, COLLECTION.topics, "collection")


@router.delete("/imports/{source_id}", status_code=204)
async def delete_import(source_id: uuid.UUID, db: AsyncSession = Depends(get_session)) -> Response:
    if not await materials.delete_source(
        db, source_id=source_id, user_id=current_user_id()
    ):
        raise HTTPException(status_code=404, detail="material not found")
    return Response(status_code=204)
