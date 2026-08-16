"""Study Plan HTTP surface. See docs/STUDY-PLAN-SPEC.md §API.

Two rules shape this module:

* **No Claude call on a read.** Plan reads, week reads, item reads, schedule
  application, Today's summary, and reminder handling all resolve from the
  database. The importer runs on `POST /study-plans/preview` and the card gate on
  `POST /.../card-proposals`, both of which are explicit user actions.
* **Nothing mutates the schedule without a confirmed, current proposal.** Every
  write that could move work recomputes the proposal server-side from the
  request's inputs and refuses a stale `base_plan_revision` with a 409.
"""

import asyncio
import logging
import uuid
from contextlib import suppress
from dataclasses import replace
from datetime import UTC, date, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy import and_, func, or_, update
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.auth import current_user_id
from app.config import get_settings
from app.db import get_session, session_factory
from app.models import (
    ACCEPTANCE_COMMITTED,
    ACCEPTANCE_FAILED,
    ACCEPTANCE_PROCESSING,
    DISPOSITION_ACCEPTED,
    DISPOSITION_EXISTING,
    DISPOSITION_SKIPPED,
    DISPOSITION_SUGGESTED,
    DRAFT_FAILED,
    DRAFT_PENDING,
    DRAFT_READY,
    DUPLICATE_EXACT,
    ITEM_COMPLETE,
    ITEM_DEFERRED,
    ITEM_LEARN,
    ITEM_PENDING,
    ITEM_PRACTICE,
    ITEM_REMOVED,
    ITEM_RETRIEVE,
    MODE_FIXED,
    ORIGIN_GENERATED,
    PLAN_ACTIVE,
    PLAN_ARCHIVED,
    PLAN_COMPLETED,
    PLAN_PAUSED,
    PRIORITY_CORE,
    PRIORITY_OPTIONAL,
    REVISION_ACTIVATE,
    REVISION_CAPACITY,
    REVISION_ITEM_EDIT,
    REVISION_REOPEN,
    REVISION_REPLAN,
    REVISION_RESUME,
    Card,
    MaterialSource,
    StudyPlan,
    StudyPlanCardLink,
    StudyPlanCardProposal,
    StudyPlanCardProposalAcceptance,
    StudyPlanDuplication,
    StudyPlanGuideDraft,
    StudyPlanItem,
    StudyPlanPracticeDebrief,
    StudyPlanRevision,
    StudyPlanWeek,
)
from app.routers.deps import local_today
from app.schemas import (
    ActivePlanSummary,
    ApplyReplan,
    CapacityUpdate,
    CardAcceptIn,
    CardAcceptOut,
    CardProposalList,
    CardProposalOut,
    CheckRow,
    DuplicateResolution,
    GuidePreviewIn,
    ItemDetail,
    ItemEdit,
    ItemMoveOut,
    PlanCreate,
    PlanList,
    PlanListEntry,
    PlanOverview,
    PlanPhaseRow,
    PlanRecap,
    PlanWeekRow,
    PracticeDebriefDraftIn,
    PracticeDebriefOut,
    PracticeDebriefSubmitIn,
    PreviewEdit,
    PreviewOut,
    PreviewPhase,
    ProposalOut,
    ReplanRequest,
    RevisionOut,
    UnresolvedOut,
    WeekDetail,
    WeekItemRow,
    WeekPlacementOut,
    WeekSection,
)
from app.services import guide_import, llm, materials, storage, usage
from app.services import study_plan as sp
from app.services import study_plan_import as spi
from app.services import study_plan_scheduler as sched
from app.services.card_lifecycle import (
    Grounding,
    GroundingError,
    active_card_filter,
    build_grounded_card,
    clean_rubric,
    lock_topic_creation,
    storage_rubric,
)
from app.services.cards import recall_is_available
from app.services.scoring_contract import (
    SCORING_CONTRACT_V2,
    active_scoring_contract_version,
)

router = APIRouter(tags=["study-plan"])

_INTERRUPTED_IMPORT_ERROR = "Import interrupted before completion. Retry to continue."

WEEKDAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")

# Stated on every card-acceptance screen, in both directions: these cards join
# the review schedule, and their reviews never come back and change this plan.
CARD_BOUNDARY_NOTE = (
    "These cards join Today's review schedule. Their reviews don't change this Study Plan."
)


def _now() -> datetime:
    return datetime.now(UTC)


# --- loading ----------------------------------------------------------------


async def _get_plan(
    db: AsyncSession, plan_id: uuid.UUID, *, for_update: bool = False
) -> StudyPlan:
    statement = select(StudyPlan).where(
        col(StudyPlan.id) == plan_id,
        StudyPlan.user_id == current_user_id(),
    )
    if for_update:
        statement = statement.with_for_update().execution_options(populate_existing=True)
    plan = (await db.exec(statement)).first()
    if plan is None:
        raise HTTPException(status_code=404, detail="plan not found")
    return plan


async def _get_graph(
    db: AsyncSession, plan_id: uuid.UUID, *, for_update: bool = False
) -> sp.PlanGraph:
    return await sp.load_plan_graph(
        db, await _get_plan(db, plan_id, for_update=for_update)
    )


def _available_item(graph: sp.PlanGraph, item_id: uuid.UUID) -> StudyPlanItem:
    """A retired curriculum row is history, never an addressable activity."""
    item = graph.item_by_id.get(item_id)
    if item is None or item.status == ITEM_REMOVED:
        raise HTTPException(status_code=404, detail="item not found")
    return item


async def _commit_activation(db: AsyncSession) -> None:
    """Commit a transaction that made a plan active.

    The partial unique index is the last line of defence against two active
    plans; a concurrent activation lands here rather than corrupting the state.
    """
    try:
        await db.commit()
    except IntegrityError as exc:  # pragma: no cover; the partial index backstop
        await db.rollback()
        raise HTTPException(status_code=409, detail="another plan is already active") from exc


def _require_current(plan: StudyPlan, base_plan_revision: int) -> None:
    """A proposal computed against an older plan is refused, not merged.

    409 rather than 422: the request was well-formed, the world moved. The client
    regenerates the proposal and shows the user the new arithmetic.
    """
    if plan.revision != base_plan_revision:
        raise HTTPException(status_code=409, detail="plan has changed since this proposal")


async def _claim_plan_mutation(
    db: AsyncSession,
    plan: StudyPlan,
    *,
    expected_revision: int | None = None,
    conflict_detail: str = "plan has changed since this proposal",
) -> int:
    """Claim one revision before staging a material plan write.

    The plan-row lock used by callers is the production serialization boundary;
    the conditional revision update is the SQLite/cross-database backstop.
    """
    base_revision = await sp.claim_plan_revision(
        db,
        plan,
        expected_revision=expected_revision,
    )
    if base_revision is None:
        await db.rollback()
        raise HTTPException(status_code=409, detail=conflict_detail)
    return base_revision


async def _fresh_completion_keys_for_plan(
    db: AsyncSession, plan_id: uuid.UUID
) -> set[str]:
    """Return every item key ever marked as fresh work for this plan.

    ``requires_fresh_completion`` is deliberately release-local in the source
    manifest. The durable revision ledger records each release's keys, and the
    completion boundary uses their union: a later manifest omitting the marker
    must not make an older client safe again by accident.
    """
    revisions = (
        await db.exec(
            select(StudyPlanRevision).where(
                col(StudyPlanRevision.plan_id) == plan_id
            )
        )
    ).all()
    keys: set[str] = set()
    for revision in revisions:
        after = revision.after or {}
        raw_keys = after.get("fresh_completion_keys", [])
        if not isinstance(raw_keys, list) or any(
            not isinstance(key, str) or not key.strip() for key in raw_keys
        ):
            # This is operator-authored metadata. Fail closed rather than let a
            # malformed ledger silently remove the view-bound precondition.
            raise HTTPException(
                status_code=409,
                detail="curriculum completion policy is unavailable; reopen the item",
            )
        keys.update(key.strip() for key in raw_keys)
    return keys


def _forecast_label(plan: StudyPlan, week: int) -> str:
    return sched.forecast_label(plan.start_date, week)


def _block_label(item: StudyPlanItem) -> str | None:
    if item.study_block_weekday is None or item.study_block_minute_of_day is None:
        return None
    hour, minute = divmod(item.study_block_minute_of_day, 60)
    return f"{WEEKDAYS[item.study_block_weekday - 1]} {hour:02d}:{minute:02d}"


def _source_recall_eligible(subject_slug: str, item: StudyPlanItem) -> bool:
    """The shared fail-closed boundary for source-grounded recall work."""
    return (
        sp.slug_supports_cards(subject_slug)
        and item.recall_supported
        and bool(item.source_excerpt.strip())
    )


def _debrief_eligible(subject_slug: str, item: StudyPlanItem) -> bool:
    """Whether this item may collect a gap that can safely become a card.

    A Practice item without a trusted source excerpt can still be completed, but
    the user's reflection cannot supply its own answer key. In that case the
    debrief prompt is deliberately absent instead of ending in a predictable
    no-card result.
    """
    return (
        item.type == ITEM_PRACTICE
        and item.status == ITEM_COMPLETE
        and _source_recall_eligible(subject_slug, item)
    )


async def _debrief_context(
    db: AsyncSession, plan_id: uuid.UUID, item_id: uuid.UUID
) -> tuple[StudyPlan, StudyPlanItem]:
    plan = await _get_plan(db, plan_id)
    item = (
        await db.exec(
            select(StudyPlanItem)
            .where(col(StudyPlanItem.id) == item_id)
            .where(col(StudyPlanItem.plan_id) == plan_id)
            .where(col(StudyPlanItem.status) != ITEM_REMOVED)
        )
    ).first()
    if item is None:
        raise HTTPException(status_code=404, detail="item not found")
    return plan, item


async def _debrief_for(db: AsyncSession, item_id: uuid.UUID) -> StudyPlanPracticeDebrief | None:
    return (
        await db.exec(
            select(StudyPlanPracticeDebrief).where(
                col(StudyPlanPracticeDebrief.plan_item_id) == item_id
            )
        )
    ).first()


def _debrief_summary(text: str) -> str:
    compact = " ".join(text.split())
    if len(compact) <= 140:
        return compact
    return compact[:137].rstrip() + "…"


def _debrief_response(row: StudyPlanPracticeDebrief, proposal_count: int = 0) -> PracticeDebriefOut:
    return PracticeDebriefOut(
        id=row.id,
        plan_id=row.plan_id,
        item_id=row.plan_item_id,
        draft_text=row.draft_text,
        text=row.text,
        submitted_at=row.submitted_at,
        summary=_debrief_summary(row.text or row.draft_text),
        has_proposals=proposal_count > 0,
        proposal_count=proposal_count,
    )


async def _debrief_out(db: AsyncSession, row: StudyPlanPracticeDebrief) -> PracticeDebriefOut:
    proposal_count = (
        await db.exec(
            select(func.count(StudyPlanCardProposal.id)).where(
                col(StudyPlanCardProposal.source_plan_item_id) == row.plan_item_id
            )
        )
    ).one()
    return _debrief_response(row, proposal_count)


# --- Today ------------------------------------------------------------------


@router.get("/study-plans/active/summary", response_model=ActivePlanSummary)
async def active_summary(db: AsyncSession = Depends(get_session)) -> ActivePlanSummary:
    """Today's plan line. Zero active plans is a normal 200, not a 404.

    Declared before `/study-plans/{plan_id}` so `active` is never read as an id.
    """
    plan = await sp.active_plan(db, current_user_id())
    if plan is None:
        return ActivePlanSummary(active=False)

    weeks = (
        await db.exec(
            select(StudyPlanWeek)
            .where(col(StudyPlanWeek.plan_id) == plan.id)
            .order_by(col(StudyPlanWeek.index))
        )
    ).all()
    current = next((w for w in weeks if w.index == plan.current_week_index), None)

    phase_title = ""
    next_block = None
    if current is not None:
        from app.models import StudyPlanPhase

        phase = (
            await db.exec(select(StudyPlanPhase).where(col(StudyPlanPhase.id) == current.phase_id))
        ).first()
        if phase is not None:
            phase_title = sp.display_title(phase.full_title, phase.overview_title)

        blocks = (
            await db.exec(
                select(StudyPlanItem)
                .where(col(StudyPlanItem.week_id) == current.id)
                .where(col(StudyPlanItem.status) == ITEM_PENDING)
                .where(col(StudyPlanItem.study_block_weekday).is_not(None))
                .order_by(
                    col(StudyPlanItem.study_block_weekday),
                    col(StudyPlanItem.study_block_minute_of_day),
                )
                .limit(1)
            )
        ).all()
        if blocks:
            next_block = _block_label(blocks[0])

    return ActivePlanSummary(
        active=True,
        plan_id=plan.id,
        title=plan.title,
        subject=plan.subject,
        week_index=plan.current_week_index,
        week_total=len(weeks),
        phase_title=phase_title,
        next_block_label=next_block,
    )


# --- preview and creation ---------------------------------------------------


def _preview_response(draft: StudyPlanGuideDraft) -> PreviewOut:
    preview = draft.preview or {}
    checks = [
        CheckRow(**{k: v for k, v in c.items() if k != "item_keys"}) for c in (draft.checks or [])
    ]
    weeks = preview.get("weeks", [])
    phases = preview.get("phases", [])

    phase_rows = []
    for phase in phases:
        owned = [w["index"] for w in weeks if w["phase_index"] == phase["index"]]
        span = (
            f"Weeks {min(owned)}–{max(owned)}"
            if owned and min(owned) != max(owned)
            else (f"Week {owned[0]}" if owned else "")
        )
        phase_rows.append(
            PreviewPhase(
                index=phase["index"],
                display_title=phase["overview_title"] or phase["full_title"],
                full_title=phase["full_title"],
                description=phase["description"],
                week_range=span,
            )
        )

    last_week = max((w["index"] for w in weeks), default=0)
    start = date.fromisoformat(preview["start_date"]) if preview.get("start_date") else None
    # Same helper the plan screens use, so the label the user reads before
    # committing cannot drift from the one they read afterwards.
    forecast = sched.forecast_label(start, last_week) if start and last_week else ""

    return PreviewOut(
        draft_id=draft.id,
        status=draft.status,
        title=preview.get("title", ""),
        subject=preview.get("subject", ""),
        mode=draft.mode,
        weeks=len(weeks),
        phases=len(phases),
        weekly_capacity_minutes=draft.weekly_capacity_minutes,
        total_minutes=preview.get("total_minutes", 0),
        forecast_label=forecast,
        supports_recall_cards=bool(preview.get("supports_recall_cards")),
        checks=checks,
        can_create=bool(checks) and all(c.status == "ok" for c in checks),
        phase_rows=phase_rows,
        error=draft.error,
    )


async def _claim_preview_import(
    db: AsyncSession,
    *,
    draft_id: uuid.UUID,
    user_id: uuid.UUID,
    expected_status: str,
) -> tuple[StudyPlanGuideDraft, uuid.UUID] | None:
    """Atomically bind one request to a pending draft or one failed Retry."""
    now = datetime.now(UTC)
    run_id = uuid.uuid4()
    statement = (
        update(StudyPlanGuideDraft)
        .where(
            StudyPlanGuideDraft.id == draft_id,
            StudyPlanGuideDraft.user_id == user_id,
            StudyPlanGuideDraft.status == expected_status,
        )
        .values(
            # A failed Retry becomes pending while its one paid call runs.  The
            # public response schema already models this durable state.
            status=DRAFT_PENDING,
            processing_run_id=run_id,
            processing_heartbeat_at=now,
            error="",
            updated_at=now,
        )
        .returning(StudyPlanGuideDraft.id)
    )
    if expected_status == DRAFT_PENDING:
        # Only the newly-created, as-yet-unclaimed draft may enter this path.
        statement = statement.where(StudyPlanGuideDraft.processing_run_id.is_(None))

    claimed_id = (await db.exec(statement)).one_or_none()
    if claimed_id is None:
        await db.rollback()
        return None
    await db.commit()
    draft = await db.get(StudyPlanGuideDraft, claimed_id, populate_existing=True)
    if draft is None:  # Concurrent account deletion cascaded immediately after claim.
        await db.rollback()
        return None
    return draft, run_id


async def _heartbeat_preview_import(draft_id: uuid.UUID, run_id: uuid.UUID) -> None:
    """Renew a live preview lease without pinning the request's connection."""
    consecutive_failures = 0
    while True:
        await asyncio.sleep(guide_import.GUIDE_IMPORT_HEARTBEAT_SECONDS)
        try:
            async with session_factory() as heartbeat_db:
                result = await heartbeat_db.exec(
                    update(StudyPlanGuideDraft)
                    .where(
                        StudyPlanGuideDraft.id == draft_id,
                        StudyPlanGuideDraft.status == DRAFT_PENDING,
                        StudyPlanGuideDraft.processing_run_id == run_id,
                    )
                    .values(processing_heartbeat_at=datetime.now(UTC))
                )
                await heartbeat_db.commit()
                if result.rowcount != 1:
                    raise HTTPException(
                        status_code=409, detail="preview import claim lost"
                    )
            consecutive_failures = 0
        except HTTPException:
            raise
        except SQLAlchemyError as exc:
            consecutive_failures += 1
            logging.getLogger(__name__).warning(
                "study-plan preview heartbeat failed draft_id=%s attempt=%d",
                draft_id,
                consecutive_failures,
            )
            if consecutive_failures >= 3:
                raise HTTPException(
                    status_code=503,
                    detail="preview import lease could not be renewed",
                ) from exc


async def _lock_preview_result(
    db: AsyncSession,
    *,
    draft_id: uuid.UUID,
    user_id: uuid.UUID,
    run_id: uuid.UUID,
) -> StudyPlanGuideDraft:
    """Lock account then child and prove this response still owns its lease."""
    if not await usage.lock_account_for_provider_result(db, user_id):
        await db.rollback()
        raise HTTPException(status_code=401, detail="unauthorized")
    draft = (
        await db.exec(
            select(StudyPlanGuideDraft)
            .where(
                StudyPlanGuideDraft.id == draft_id,
                StudyPlanGuideDraft.user_id == user_id,
                StudyPlanGuideDraft.status == DRAFT_PENDING,
                StudyPlanGuideDraft.processing_run_id == run_id,
                StudyPlanGuideDraft.processing_heartbeat_at
                > datetime.now(UTC) - guide_import.GUIDE_IMPORT_STALE_AFTER,
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    ).first()
    if draft is None:
        await db.rollback()
        raise HTTPException(status_code=409, detail="preview import claim lost")
    return draft


async def _store_preview_failure(
    db: AsyncSession,
    *,
    draft_id: uuid.UUID,
    user_id: uuid.UUID,
    run_id: uuid.UUID,
    error: str,
) -> None:
    """Best-effort terminal write, conditional on this request's exact token."""
    try:
        draft = await _lock_preview_result(
            db,
            draft_id=draft_id,
            user_id=user_id,
            run_id=run_id,
        )
    except HTTPException:
        return
    draft.status = DRAFT_FAILED
    draft.error = error
    draft.processing_run_id = None
    draft.processing_heartbeat_at = None
    draft.updated_at = datetime.now(UTC)
    db.add(draft)
    await db.commit()


def _preview_import_authorizer(
    db: AsyncSession,
    *,
    draft_id: uuid.UUID,
    user_id: uuid.UUID,
    run_id: uuid.UUID,
):
    """Authorize only while this request owns the exact durable draft lease."""
    config = get_settings()

    async def require_live_claim(boundary_db: AsyncSession) -> None:
        draft = (
            await boundary_db.exec(
                select(StudyPlanGuideDraft.id)
                .where(
                    StudyPlanGuideDraft.id == draft_id,
                    StudyPlanGuideDraft.user_id == user_id,
                    StudyPlanGuideDraft.status == DRAFT_PENDING,
                    StudyPlanGuideDraft.processing_run_id == run_id,
                    StudyPlanGuideDraft.processing_heartbeat_at
                    > datetime.now(UTC) - guide_import.GUIDE_IMPORT_STALE_AFTER,
                )
                .with_for_update()
            )
        ).first()
        if draft is None:
            raise HTTPException(status_code=409, detail="preview import claim lost")

    return usage.provider_call_authorizer(
        db,
        user_id,
        "guide_import",
        config=config,
        provider="anthropic",
        model=config.studyplan_model,
        boundary_check=require_live_claim,
    )


async def _run_import(
    draft_id: uuid.UUID,
    user_id: uuid.UUID,
    db: AsyncSession,
    *,
    expected_status: str,
) -> StudyPlanGuideDraft:
    """Claim, import, and store only the current request's provider result.

    New previews claim ``pending`` once. Retry claims ``failed`` once and moves
    it back to ``pending`` before any transmission, so simultaneous retries
    cannot both authorize or send the full guide. The long provider await holds
    no database lock or connection; only the short authorization, heartbeat,
    and claimant-only result transactions touch the database.
    """
    claim = await _claim_preview_import(
        db,
        draft_id=draft_id,
        user_id=user_id,
        expected_status=expected_status,
    )
    if claim is None:
        raise HTTPException(status_code=409, detail="preview import is not retryable")
    claimed, run_id = claim
    # Import into a detached working copy.  Re-reading the claimed row under
    # FOR UPDATE after the provider call cannot erase the in-memory result.
    working = StudyPlanGuideDraft.model_validate(claimed.model_dump())
    heartbeat = asyncio.create_task(
        _heartbeat_preview_import(draft_id, run_id),
        name=f"study-plan-preview-heartbeat-{draft_id}",
    )
    try:
        await guide_import.run_while_heartbeat_live(
            guide_import.run_plan_draft(
                working,
                before_provider_call=_preview_import_authorizer(
                    db,
                    draft_id=draft_id,
                    user_id=user_id,
                    run_id=run_id,
                ),
            ),
            heartbeat,
        )
        draft = await _lock_preview_result(
            db,
            draft_id=draft_id,
            user_id=user_id,
            run_id=run_id,
        )
        guide_import.copy_plan_draft_result(draft, working)
        draft.processing_run_id = None
        draft.processing_heartbeat_at = None
        db.add(draft)
        await db.commit()
        await db.refresh(draft)
        return draft
    except Exception as exc:
        logging.getLogger(__name__).error(
            "study-plan preview failed draft_id=%s type=%s",
            draft_id,
            type(exc).__name__,
        )
        await _store_preview_failure(
            db,
            draft_id=draft_id,
            user_id=user_id,
            run_id=run_id,
            error="Preview import failed. Retry to continue.",
        )
        if isinstance(exc, HTTPException):
            raise
        if isinstance(exc, llm.LLMError):
            raise
        raise HTTPException(status_code=503, detail="preview_import_failed") from exc
    finally:
        if not heartbeat.done():
            heartbeat.cancel()
        with suppress(asyncio.CancelledError, HTTPException):
            await heartbeat


def _revalidate(draft: StudyPlanGuideDraft) -> None:
    """Re-run the gate over the stored response plus the user's resolutions."""
    guide_import.revalidate_plan_draft(draft)


@router.post("/study-plans/preview", response_model=PreviewOut, status_code=201)
async def preview_guide(
    body: GuidePreviewIn, db: AsyncSession = Depends(get_session)
) -> PreviewOut:
    start = body.start_date or spi.default_start_date(await local_today(db))
    errors = spi.validate_request(
        guide_text=body.guide_text,
        requested_weeks=body.requested_weeks,
        weekly_capacity_minutes=body.weekly_capacity_minutes,
        mode=body.mode,
        deadline=body.deadline,
        start_date=start,
    )
    if errors:
        raise HTTPException(status_code=422, detail="; ".join(errors))

    await storage.reserve_study_guide_draft(
        db, user_id=current_user_id(), characters=len(body.guide_text)
    )
    draft = StudyPlanGuideDraft(
        user_id=current_user_id(),
        guide_text=body.guide_text,
        requested_weeks=body.requested_weeks,
        weekly_capacity_minutes=body.weekly_capacity_minutes,
        mode=body.mode,
        deadline=body.deadline,
        start_date=start,
        subject_hint=body.subject_hint,
        title_hint=body.title_hint,
    )
    # Saved before the call, so a crash mid-import still leaves the guide.
    db.add(draft)
    await db.commit()

    draft = await _run_import(
        draft.id,
        draft.user_id,
        db,
        expected_status=DRAFT_PENDING,
    )
    return _preview_response(draft)


async def _get_draft(db: AsyncSession, draft_id: uuid.UUID) -> StudyPlanGuideDraft:
    draft = (
        await db.exec(
            select(StudyPlanGuideDraft)
            .where(col(StudyPlanGuideDraft.id) == draft_id)
            .where(StudyPlanGuideDraft.user_id == current_user_id())
        )
    ).first()
    if draft is None:
        raise HTTPException(status_code=404, detail="draft not found")
    if draft.status == DRAFT_PENDING:
        # A process can die after durably claiming but before it stores a
        # terminal state. Expire only an unclaimed/heartbeat-stale row; a live
        # eleven-minute request keeps renewing and remains pending.
        stale_before = datetime.now(UTC) - guide_import.GUIDE_IMPORT_STALE_AFTER
        expired_id = (
            await db.exec(
                update(StudyPlanGuideDraft)
                .where(
                    StudyPlanGuideDraft.id == draft.id,
                    StudyPlanGuideDraft.user_id == current_user_id(),
                    StudyPlanGuideDraft.status == DRAFT_PENDING,
                    or_(
                        and_(
                            StudyPlanGuideDraft.processing_run_id.is_(None),
                            StudyPlanGuideDraft.updated_at <= stale_before,
                        ),
                        and_(
                            StudyPlanGuideDraft.processing_run_id.is_not(None),
                            or_(
                                StudyPlanGuideDraft.processing_heartbeat_at.is_(None),
                                StudyPlanGuideDraft.processing_heartbeat_at <= stale_before,
                            ),
                        ),
                    ),
                )
                .values(
                    status=DRAFT_FAILED,
                    error=_INTERRUPTED_IMPORT_ERROR,
                    processing_run_id=None,
                    processing_heartbeat_at=None,
                    updated_at=datetime.now(UTC),
                )
                .returning(StudyPlanGuideDraft.id)
            )
        ).one_or_none()
        if expired_id is not None:
            await db.commit()
            refreshed = await db.get(
                StudyPlanGuideDraft, draft.id, populate_existing=True
            )
            if refreshed is None:
                raise HTTPException(status_code=404, detail="draft not found")
            draft = refreshed
    return draft


@router.get("/study-plans/preview/{draft_id}", response_model=PreviewOut)
async def get_preview(draft_id: uuid.UUID, db: AsyncSession = Depends(get_session)) -> PreviewOut:
    """Return a durable import result without spending another model call."""
    return _preview_response(await _get_draft(db, draft_id))


@router.post("/study-plans/preview/{draft_id}/retry", response_model=PreviewOut)
async def retry_preview(draft_id: uuid.UUID, db: AsyncSession = Depends(get_session)) -> PreviewOut:
    draft = await _get_draft(db, draft_id)
    if draft.status != DRAFT_FAILED:
        raise HTTPException(status_code=409, detail="only a failed preview can be retried")
    draft = await _run_import(
        draft.id,
        draft.user_id,
        db,
        expected_status=DRAFT_FAILED,
    )
    return _preview_response(draft)


@router.patch("/study-plans/preview/{draft_id}", response_model=PreviewOut)
async def edit_preview(
    draft_id: uuid.UUID, body: PreviewEdit, db: AsyncSession = Depends(get_session)
) -> PreviewOut:
    """Apply edits and review decisions, then re-run the gate over them.

    Edits are applied to the stored raw response rather than the preview, so a
    later re-validation sees them too. An edit that only lived on the preview
    would silently revert on the next check.
    """
    draft = await _get_draft(db, draft_id)
    if not draft.raw_response:
        raise HTTPException(status_code=409, detail="draft has no import to edit")

    raw = dict(draft.raw_response)
    raw_items = [item for item in raw.get("items", []) if isinstance(item, dict)]
    item_keys = {
        str(item.get("key", "")).strip()
        for item in raw_items
        if str(item.get("key", "")).strip()
    }
    generated_retrieval_keys = {
        str(item.get("key", "")).strip()
        for item in raw_items
        if item.get("type") == ITEM_RETRIEVE
        and item.get("origin") == ORIGIN_GENERATED
        and str(item.get("key", "")).strip()
    }
    dependency_keys = {
        f"{dependency.get('prerequisite_key')}->{dependency.get('dependent_key')}"
        for dependency in raw.get("dependencies", [])
        if isinstance(dependency, dict)
        and dependency.get("prerequisite_key")
        and dependency.get("dependent_key")
    }
    overview_keys = {
        f"{collection[:-1]}-{row.get('index')}"
        for collection in ("phases", "weeks")
        for row in raw.get(collection, [])
        if isinstance(row, dict) and row.get("index") is not None
    }

    def require_known_keys(values, allowed: set[str], *, field: str) -> None:
        if set(values) - allowed:
            raise HTTPException(
                status_code=422,
                detail={"code": "invalid_preview_edit", "field": field},
            )

    require_known_keys(body.item_estimates, item_keys, field="item_estimates")
    require_known_keys(body.overview_titles, overview_keys, field="overview_titles")
    if body.item_estimates:
        items = [dict(item) for item in raw_items]
        for item in items:
            new_estimate = body.item_estimates.get(item.get("key", ""))
            if new_estimate:
                item["estimate_minutes"] = new_estimate
                item["estimate_source"] = "user_edited"
                item["estimate_confidence"] = "high"
        raw["items"] = items
    if body.overview_titles:
        for collection in ("phases", "weeks"):
            rows = [dict(r) for r in raw.get(collection, [])]
            for row in rows:
                key = f"{collection[:-1]}-{row.get('index')}"
                if key in body.overview_titles:
                    row["overview_title"] = body.overview_titles[key]
            raw[collection] = rows
    draft.raw_response = raw

    preview = dict(draft.preview or {})
    resolutions = dict(preview.get("resolutions", {}))
    resolution_domains = {
        "estimates_reviewed": item_keys,
        "retrieval_approved": generated_retrieval_keys,
        "retrieval_rejected": generated_retrieval_keys,
        "dependencies_confirmed": dependency_keys,
    }
    for field_name, allowed in resolution_domains.items():
        value = getattr(body, field_name)
        require_known_keys(value or (), allowed, field=field_name)
        # Older rows may predate these domain checks. Revalidation keeps only
        # decisions tied to the immutable import graph, so arbitrary stale keys
        # cannot remain as a cumulative JSON-storage side channel.
        existing = set(resolutions.get(field_name, [])) & allowed
        if value is not None:
            existing.update(value)
        resolutions[field_name] = sorted(existing)
    if set(resolutions["retrieval_approved"]) & set(
        resolutions["retrieval_rejected"]
    ):
        raise HTTPException(
            status_code=422,
            detail={"code": "invalid_preview_edit", "field": "retrieval_decision"},
        )
    if body.omissions_acknowledged is not None:
        resolutions["omissions_acknowledged"] = body.omissions_acknowledged
    preview["resolutions"] = resolutions
    draft.preview = preview

    _revalidate(draft)
    db.add(draft)
    await db.commit()
    await db.refresh(draft)
    return _preview_response(draft)


@router.post("/study-plans", response_model=PlanOverview, status_code=201)
async def create_plan(body: PlanCreate, db: AsyncSession = Depends(get_session)) -> PlanOverview:
    """Create a plan from a resolved preview. One transaction, no model call."""
    draft = await _get_draft(db, body.draft_id)
    if draft.status != DRAFT_READY:
        raise HTTPException(status_code=409, detail="draft is not ready")
    if not all(c.get("status") == "ok" for c in (draft.checks or [])):
        raise HTTPException(status_code=409, detail="draft has unresolved checks")

    await storage.reserve_study_plan(
        db, user_id=current_user_id(), characters=len(draft.guide_text)
    )

    rows = spi.build_plan_rows(draft.preview, start_date=draft.start_date)
    plan: StudyPlan = rows["plan"]
    plan.user_id = current_user_id()

    previous_active = await sp.active_plan(db, current_user_id()) if body.activate else None
    if body.activate:
        plan.status = PLAN_ACTIVE
        if previous_active is not None:
            # Making another plan active pauses the current one. The client has
            # already confirmed this; the database's partial unique index is what
            # guarantees the two never overlap. It is evaluated per statement,
            # so the pause has to reach the database before the activation does.
            previous_active.status = PLAN_PAUSED
            previous_active.paused_at = _now()
            previous_active.updated_at = previous_active.paused_at
            db.add(previous_active)
            await db.flush()
    else:
        plan.status = PLAN_PAUSED
        plan.paused_at = _now()

    await sp.insert_plan_rows(db, plan, rows)
    db.add(
        StudyPlanRevision(
            plan_id=plan.id,
            kind="created",
            base_plan_revision=1,
            before={},
            after={"weeks": len(rows["weeks"]), "items": len(rows["items"])},
            summary=f"Plan created from a guide · {len(rows['items'])} items",
        )
    )
    source = (
        await db.exec(
            select(MaterialSource).where(
                MaterialSource.plan_draft_id == draft.id,
                MaterialSource.user_id == current_user_id(),
            )
        )
    ).first()
    if source is not None:
        await materials.confirm_source_version(db, source, current_user_id())

    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        # Distinguished, because these have completely different fixes and the
        # blanket message sent a rubric violation ("type": "review" reaching a
        # CHECK constraint) to a user reading "another plan is already active".
        detail = (
            "another plan is already active"
            if "uq_study_plans_one_active" in str(exc)
            else "the imported plan has a value the schema rejects; retry the import"
        )
        raise HTTPException(status_code=409, detail=detail) from exc

    return await _overview(db, plan.id)


# --- reads ------------------------------------------------------------------


@router.get("/study-plans", response_model=PlanList)
async def list_plans(db: AsyncSession = Depends(get_session)) -> PlanList:
    plans = (
        await db.exec(
            select(StudyPlan)
            .where(StudyPlan.user_id == current_user_id())
            .order_by(col(StudyPlan.created_at).desc())
        )
    ).all()
    buckets: dict[str, list[PlanListEntry]] = {
        PLAN_ACTIVE: [],
        PLAN_PAUSED: [],
        PLAN_COMPLETED: [],
        PLAN_ARCHIVED: [],
    }
    # Two queries for the whole list rather than two per plan. The Plans sheet
    # shows every plan the user has ever made, so the per-plan version grew a
    # query every time one was archived instead of deleted.
    plan_ids = [plan.id for plan in plans]
    all_weeks = (
        (await db.exec(select(StudyPlanWeek).where(col(StudyPlanWeek.plan_id).in_(plan_ids)))).all()
        if plan_ids
        else []
    )
    all_items = (
        (await db.exec(select(StudyPlanItem).where(col(StudyPlanItem.plan_id).in_(plan_ids)))).all()
        if plan_ids
        else []
    )
    week_counts: dict[uuid.UUID, int] = {}
    for week in all_weeks:
        week_counts[week.plan_id] = week_counts.get(week.plan_id, 0) + 1
    items_by_plan: dict[uuid.UUID, list[StudyPlanItem]] = {}
    for item in all_items:
        items_by_plan.setdefault(item.plan_id, []).append(item)

    for plan in plans:
        done, total = sp.core_progress(items_by_plan.get(plan.id, []))
        if plan.status == PLAN_ACTIVE:
            meta = (
                f"Week {plan.current_week_index} · "
                f"{sp.week_of_label(plan, plan.forecast_end_plan_week)}"
            )
        elif plan.status == PLAN_PAUSED:
            when = plan.paused_at.strftime("%-d %b") if plan.paused_at else ""
            meta = (
                f"Paused {when} · Week {plan.current_week_index} of {week_counts.get(plan.id, 0)}"
            )
        elif plan.status == PLAN_COMPLETED:
            when = plan.completed_at.strftime("%-d %b") if plan.completed_at else ""
            meta = f"Completed {when} · {done} of {total} Core"
        else:
            when = plan.archived_at.strftime("%-d %b") if plan.archived_at else ""
            meta = f"Archived {when} · {done} of {total} Core"
        buckets[plan.status].append(
            PlanListEntry(
                id=plan.id,
                title=plan.title,
                subject=plan.subject,
                status=plan.status,
                meta=meta,
                created_at=plan.created_at,
            )
        )
    return PlanList(
        active=buckets[PLAN_ACTIVE],
        paused=buckets[PLAN_PAUSED],
        completed=buckets[PLAN_COMPLETED],
        archived=buckets[PLAN_ARCHIVED],
    )


async def _overview(db: AsyncSession, plan_id: uuid.UUID) -> PlanOverview:
    graph = await _get_graph(db, plan_id)
    plan = graph.plan

    current_week = graph.week_by_index.get(plan.current_week_index)
    current_phase_index = 1
    if current_week is not None:
        phase = graph.phase_of_week(current_week)
        if phase is not None:
            current_phase_index = phase.index

    phase_rows = []
    for phase in graph.phases:
        owned = [w for w in graph.weeks if w.phase_id == phase.id]
        week_rows = []
        for week in owned:
            items = [i for i in graph.items_in(week) if i.status != ITEM_REMOVED]
            all_done = bool(items) and all(i.status == ITEM_COMPLETE for i in items)
            week_rows.append(
                PlanWeekRow(
                    index=week.index,
                    display_title=sp.display_title(week.full_title, week.overview_title),
                    full_title=week.full_title,
                    status=sp.week_status(week.index, plan.current_week_index, all_done),
                    is_current=week.index == plan.current_week_index,
                )
            )
        is_current = phase.index == current_phase_index
        phase_rows.append(
            PlanPhaseRow(
                index=phase.index,
                display_title=sp.display_title(phase.full_title, phase.overview_title),
                full_title=phase.full_title,
                status=sp.phase_status(phase.index, current_phase_index),
                week_range=sp.week_range_label(owned),
                current_week_label=(f"Week {plan.current_week_index}" if is_current else None),
                weeks=week_rows,
            )
        )

    latest = (
        await db.exec(
            select(StudyPlanRevision)
            .where(col(StudyPlanRevision.plan_id) == plan.id)
            .order_by(col(StudyPlanRevision.created_at).desc())
        )
    ).first()

    return PlanOverview(
        id=plan.id,
        title=plan.title,
        subject=plan.subject,
        mode=plan.mode,
        status=plan.status,
        week_index=plan.current_week_index,
        week_total=len(graph.weeks),
        forecast_label=_forecast_label(plan, plan.forecast_end_plan_week),
        forecast_end_plan_week=plan.forecast_end_plan_week,
        revision=plan.revision,
        supports_recall_cards=sp.slug_supports_cards(plan.subject_slug),
        phases=phase_rows,
        latest_change=(latest.summary or None) if latest else None,
    )


@router.get("/study-plans/{plan_id}", response_model=PlanOverview)
async def get_overview(plan_id: uuid.UUID, db: AsyncSession = Depends(get_session)) -> PlanOverview:
    return await _overview(db, plan_id)


@router.get("/study-plans/{plan_id}/weeks/{index}", response_model=WeekDetail)
async def get_week(
    plan_id: uuid.UUID, index: int, db: AsyncSession = Depends(get_session)
) -> WeekDetail:
    graph = await _get_graph(db, plan_id)
    week = graph.week_by_index.get(index)
    if week is None:
        raise HTTPException(status_code=404, detail="week not found")

    items = graph.items_in(week)
    scheduled = [i for i in items if i.status in (ITEM_PENDING, ITEM_COMPLETE)]
    capacity = week.override_capacity_minutes or graph.plan.default_weekly_capacity_minutes
    # The scheduler owns this arithmetic. Recomputing it here was a second
    # implementation of "what is in this week", and only the scheduler's was
    # covered by tests.
    workload = sched.summarise_week(sp.to_sched_items(items, graph.weeks, graph.phases))
    core_done, core_total = workload.core_complete, workload.core_total
    planned = workload.scheduled_plan_minutes

    sections: list[WeekSection] = []
    for kind, label in (
        (ITEM_LEARN, "LEARN"),
        (ITEM_PRACTICE, "PRACTICE"),
        (ITEM_RETRIEVE, "RETRIEVE"),
    ):
        rows_in = [i for i in scheduled if i.type == kind]
        if not rows_in:
            continue
        section = sched.summarise_week(sp.to_sched_items(rows_in, graph.weeks, graph.phases))
        core, optional = section.core_minutes, section.optional_minutes
        if kind == ITEM_RETRIEVE:
            aside = (
                f"{section.retrieval_complete} of {section.retrieval_total} done "
                "· Doesn't block the week"
            )
            # V3.5 moved both of these out of the week header and under Retrieve,
            # where the boundary they describe is the thing being read.
            note = (
                "Reviews continue separately in Today. Retrieval time still counts "
                f"toward this week's {sched.format_hours(capacity)}."
            )
        else:
            parts = [f"Core · {core} min"] if core else []
            if optional:
                parts.append(f"Optional · {optional} min")
            aside = " · ".join(parts)
            note = ""
        sections.append(
            WeekSection(
                type=kind,
                label=label,
                aside=aside,
                note=note,
                rows=[
                    WeekItemRow(
                        id=i.id,
                        title=i.full_title,
                        estimate_minutes=i.estimate_minutes,
                        complete=i.status == ITEM_COMPLETE,
                        optional=i.priority == PRIORITY_OPTIONAL,
                        blocked_by=next(
                            (b.full_title for b in sp.blocking_dependencies(graph, i)), None
                        ),
                    )
                    for i in rows_in
                ],
            )
        )

    phase = graph.phase_of_week(week)
    return WeekDetail(
        plan_id=plan_id,
        index=week.index,
        phase_title=sp.display_title(phase.full_title, phase.overview_title) if phase else "",
        full_title=week.full_title,
        display_title=sp.display_title(week.full_title, week.overview_title),
        core_complete=core_done,
        core_total=core_total,
        planned_minutes=planned,
        capacity_minutes=capacity,
        core_line=f"{core_done} of {core_total} Core complete",
        capacity_line=(f"{sched.format_hours(planned)} of {sched.format_hours(capacity)} planned"),
        sections=sections,
    )


async def _item_detail(db: AsyncSession, graph: sp.PlanGraph, item: StudyPlanItem) -> ItemDetail:
    if item.status == ITEM_REMOVED:
        raise HTTPException(status_code=404, detail="item not found")
    week = graph.week_by_id.get(item.week_id)
    phase = graph.phase_by_id.get(item.phase_id)
    debrief = await _debrief_for(db, item.id)
    links = (
        await db.exec(
            select(StudyPlanCardLink).where(col(StudyPlanCardLink.plan_item_id) == item.id)
        )
    ).all()
    card_index: dict[str, Card] = {}
    if item.mapped_recall_topics:
        cards = (
            await db.exec(
                select(Card).where(
                    Card.user_id == current_user_id(),
                    active_card_filter(),
                )
            )
        ).all()
        for card in cards:
            card_index.setdefault(sp.normalize_topic(card.topic), card)

    mapped_cards = []
    for topic in item.mapped_recall_topics:
        card = card_index.get(sp.normalize_topic(topic))
        if card is None:
            mapped_cards.append(
                {
                    "topic": topic,
                    "card_id": None,
                    "availability_label": "Not ready",
                }
            )
            continue
        grounding = Grounding(
            source_url=card.source_url,
            source_section=card.source_section,
            source_label=card.source_label,
            answer_basis=card.answer_basis or card.answer_anchor,
            answer_rubric=card.answer_rubric,
            canonical_question=card.canonical_question or "",
        )
        grounded = not grounding.missing()
        mapped_cards.append(
            {
                "topic": topic,
                # An incomplete legacy row is intentionally not a navigation
                # target. The client treats a non-null id as actionable.
                "card_id": card.id if grounded else None,
                "availability_label": (
                    "Not ready"
                    if not grounded
                    else (
                        "Waiting for delayed recall"
                        if card.recall_not_before_at is not None
                        and not recall_is_available(card, _now())
                        else (
                            "In review rotation"
                            if card.last_score is not None or card.repetitions > 0
                            else (
                                "Ready for recall"
                                if card.last_learning_exposure_at is not None
                                else "Learn first"
                            )
                        )
                    )
                ),
            }
        )
    return ItemDetail(
        id=item.id,
        plan_id=item.plan_id,
        plan_revision=graph.plan.revision,
        full_title=item.full_title,
        phase_title=sp.display_title(phase.full_title, phase.overview_title) if phase else "",
        week_index=week.index if week else 0,
        type=item.type,
        priority=item.priority,
        status=item.status,
        why_it_matters=item.why_it_matters,
        done_when=item.done_when,
        estimate_minutes=item.estimate_minutes,
        estimate_source=item.estimate_source,
        estimate_confidence=item.estimate_confidence,
        source_excerpt=item.source_excerpt,
        source_label=item.parser_interpretation,
        recall_supported=item.recall_supported,
        resources=item.resources,
        mapped_recall_cards=mapped_cards,
        stretch_actions=item.stretch_actions,
        notes=item.notes,
        study_block_label=_block_label(item) or item.study_block_label,
        study_block_weekday=item.study_block_weekday,
        study_block_minute_of_day=item.study_block_minute_of_day,
        study_block_reminder_on=item.study_block_reminder_on,
        completed_at=item.completed_at,
        reopened_at=item.reopened_at,
        linked_card_ids=[link.card_id for link in links],
        card_proposals_available=(
            item.status == ITEM_COMPLETE
            and _source_recall_eligible(graph.plan.subject_slug, item)
            and (
                item.type != ITEM_PRACTICE
                or (debrief is not None and debrief.submitted_at is not None)
            )
        ),
        practice_debrief_eligible=_debrief_eligible(graph.plan.subject_slug, item),
        practice_debrief=(await _debrief_out(db, debrief)) if debrief else None,
        blocked_by=[b.full_title for b in sp.blocking_dependencies(graph, item)],
    )


@router.get("/study-plans/{plan_id}/items/{item_id}", response_model=ItemDetail)
async def get_item(
    plan_id: uuid.UUID, item_id: uuid.UUID, db: AsyncSession = Depends(get_session)
) -> ItemDetail:
    graph = await _get_graph(db, plan_id)
    item = _available_item(graph, item_id)
    return await _item_detail(db, graph, item)


@router.get("/study-plans/{plan_id}/revisions", response_model=list[RevisionOut])
async def list_revisions(
    plan_id: uuid.UUID, db: AsyncSession = Depends(get_session)
) -> list[RevisionOut]:
    await _get_plan(db, plan_id)
    rows = (
        await db.exec(
            select(StudyPlanRevision)
            .where(col(StudyPlanRevision.plan_id) == plan_id)
            .order_by(col(StudyPlanRevision.created_at).desc())
        )
    ).all()
    return [
        RevisionOut(
            id=r.id,
            kind=r.kind,
            summary=r.summary or r.kind.replace("_", " ").capitalize(),
            created_at=r.created_at,
            reversible=r.reversible,
        )
        for r in rows
    ]


@router.get("/study-plans/{plan_id}/recap", response_model=PlanRecap)
async def get_recap(plan_id: uuid.UUID, db: AsyncSession = Depends(get_session)) -> PlanRecap:
    graph = await _get_graph(db, plan_id)
    scheduled = [i for i in graph.items if i.status in (ITEM_PENDING, ITEM_COMPLETE)]
    core_done, core_total = sp.core_progress(scheduled)
    retrieval = [i for i in scheduled if i.type == ITEM_RETRIEVE]
    practice = [i for i in scheduled if i.type == ITEM_PRACTICE]
    return PlanRecap(
        id=graph.plan.id,
        title=graph.plan.title,
        core_complete=core_done,
        core_total=core_total,
        optional_deferred=sum(
            1 for i in graph.items if i.priority == PRIORITY_OPTIONAL and i.status == ITEM_DEFERRED
        ),
        estimated_minutes=sum(i.estimate_minutes for i in scheduled),
        learn_practice_minutes=sum(
            i.estimate_minutes for i in scheduled if i.type != ITEM_RETRIEVE
        ),
        retrieval_minutes=sum(i.estimate_minutes for i in retrieval),
        retrieval_completed=sum(1 for i in retrieval if i.status == ITEM_COMPLETE),
        practice_completed=sum(1 for i in practice if i.status == ITEM_COMPLETE),
        remaining_gaps=[
            i.full_title
            for i in scheduled
            if i.priority == PRIORITY_CORE and i.status != ITEM_COMPLETE
        ][:5],
    )


# --- proposals --------------------------------------------------------------


def _proposal_out(
    graph: sp.PlanGraph,
    proposal: sched.Proposal,
    *,
    kind: str,
    headline: str,
    body: str,
    unchanged: list[str],
) -> ProposalOut:
    titles = {str(i.id): i.full_title for i in graph.items}
    return ProposalOut(
        kind=kind,
        base_plan_revision=proposal.base_plan_revision,
        headline=headline,
        body=body,
        weeks=[
            WeekPlacementOut(
                index=w.index,
                capacity_minutes=w.capacity_minutes,
                before_minutes=w.before_minutes,
                after_minutes=w.after_minutes,
                changed=w.changed,
                over_capacity=w.over_capacity,
            )
            for w in proposal.weeks
        ],
        moves=[
            ItemMoveOut(
                title=titles.get(m.item_id, ""),
                from_week=m.from_week,
                to_week=m.to_week,
                minutes=m.minutes,
            )
            for m in proposal.moves
        ],
        unresolved=[
            UnresolvedOut(title=titles.get(u.item_id, ""), minutes=u.minutes, reason=u.reason)
            for u in proposal.unresolved
        ],
        unresolved_minutes=proposal.unresolved_minutes,
        displaced_minutes=proposal.displaced_minutes,
        forecast_end_plan_week=proposal.forecast_end_plan_week,
        forecast_label=_forecast_label(graph.plan, proposal.forecast_end_plan_week),
        forecast_changed=proposal.forecast_changed,
        can_apply=proposal.valid,
        reasons=list(proposal.reasons),
        unchanged=unchanged,
    )


UNCHANGED_ALWAYS = [
    "Your cards, scores, session history, mastery summaries, and review schedule are untouched.",
    "Completed work stays in the week you did it.",
]


def _replan_kwargs(body: ReplanRequest) -> dict[str, Any]:
    return {
        "capacity_overrides": {int(k): v for k, v in body.capacity_overrides.items()},
        "default_capacity_minutes": body.default_capacity_minutes,
        "deferred_item_ids": [str(i) for i in body.deferred_item_ids],
        "extra_weeks": body.extra_weeks,
        "insert_after_phase": body.insert_after_phase,
        "confirmed_core_removals": [str(i) for i in body.confirmed_core_removals],
    }


def _replan_copy(proposal: sched.Proposal) -> tuple[str, str]:
    """Lead with the outcome, then the arithmetic. V3.5 §3."""
    if not proposal.moves and not proposal.unresolved:
        return ("Nothing needs to move.", "Every week still fits its capacity.")
    if proposal.unresolved:
        minutes = proposal.unresolved_minutes
        return (
            f"{minutes} minutes have nowhere to go.",
            "Apply stays disabled until the selected option resolves every week. "
            "Core work is never dropped to force a fit.",
        )
    displaced = proposal.displaced_minutes
    landing = {}
    for move in proposal.moves:
        landing[move.to_week] = landing.get(move.to_week, 0) + move.minutes
    where = " · ".join(f"{m} to Week {w}" for w, m in sorted(landing.items()))
    forecast = (
        "The completion forecast moves."
        if proposal.forecast_changed
        else "The completion forecast is unchanged."
    )
    return (
        f"{displaced} minutes carry forward.",
        f"{where}. {forecast}",
    )


@router.post("/study-plans/{plan_id}/replans/preview", response_model=ProposalOut)
async def preview_replan(
    plan_id: uuid.UUID, body: ReplanRequest, db: AsyncSession = Depends(get_session)
) -> ProposalOut:
    """Compute what would happen. Writes nothing, ever."""
    graph = await _get_graph(db, plan_id)
    _require_current(graph.plan, body.base_plan_revision)
    proposal = sp.build_proposal_for(graph, **_replan_kwargs(body))
    headline, text = _replan_copy(proposal)
    return _proposal_out(
        graph,
        proposal,
        kind="replan",
        headline=headline,
        body=text,
        unchanged=UNCHANGED_ALWAYS,
    )


@router.post("/study-plans/{plan_id}/replans/apply", response_model=ProposalOut)
async def apply_replan(
    plan_id: uuid.UUID, body: ApplyReplan, db: AsyncSession = Depends(get_session)
) -> ProposalOut:
    graph = await _get_graph(db, plan_id, for_update=True)
    _require_current(graph.plan, body.base_plan_revision)
    kwargs = _replan_kwargs(body)
    proposal = sp.build_proposal_for(graph, **kwargs)
    if not proposal.valid:
        raise HTTPException(status_code=409, detail="proposal does not validate")

    headline, text = _replan_copy(proposal)
    base_revision = await _claim_plan_mutation(
        db,
        graph.plan,
        expected_revision=body.base_plan_revision,
    )
    for item_id in body.deferred_item_ids:
        item = graph.item_by_id.get(item_id)
        if item is not None and item.status == ITEM_REMOVED:
            raise HTTPException(status_code=404, detail="item not found")
        if item is not None:
            item.status = ITEM_DEFERRED
            item.updated_at = _now()
            db.add(item)

    sp.apply_proposal(
        db,
        graph,
        proposal,
        base_plan_revision=base_revision,
        kind=REVISION_REPLAN,
        summary=headline,
        capacity_overrides=kwargs["capacity_overrides"],
        default_capacity_minutes=kwargs["default_capacity_minutes"],
    )
    await db.commit()
    return _proposal_out(
        graph,
        proposal,
        kind="replan",
        headline=headline,
        body=text,
        unchanged=UNCHANGED_ALWAYS,
    )


@router.patch("/study-plans/{plan_id}/weeks/{index}/capacity", response_model=ProposalOut)
async def update_capacity(
    plan_id: uuid.UUID,
    index: int,
    body: CapacityUpdate,
    db: AsyncSession = Depends(get_session),
) -> ProposalOut:
    """Preview a capacity change. Applied only when nothing has to move.

    A change that displaces work returns the proposal with `can_apply` reflecting
    validity and writes nothing. The user confirms it through the replan path,
    which is the same code with an explicit confirmation in front of it.
    """
    graph = await _get_graph(db, plan_id, for_update=True)
    _require_current(graph.plan, body.base_plan_revision)
    if index not in graph.week_by_index:
        raise HTTPException(status_code=404, detail="week not found")

    overrides = {index: body.override_capacity_minutes}
    proposal = sp.build_proposal_for(graph, capacity_overrides=overrides)
    headline, text = _replan_copy(proposal)

    if proposal.valid and not proposal.moves:
        effective = body.override_capacity_minutes or graph.plan.default_weekly_capacity_minutes
        base_revision = await _claim_plan_mutation(
            db,
            graph.plan,
            expected_revision=body.base_plan_revision,
        )
        sp.apply_proposal(
            db,
            graph,
            proposal,
            base_plan_revision=base_revision,
            kind=REVISION_CAPACITY,
            summary=f"Week {index} capacity set to {sched.format_hours(effective)}",
            capacity_overrides=overrides,
            reversible=True,
        )
        await db.commit()

    return _proposal_out(
        graph,
        proposal,
        kind="capacity",
        headline=headline,
        body=text,
        unchanged=UNCHANGED_ALWAYS,
    )


# --- item progress ----------------------------------------------------------


async def _complete_item_cas(
    db: AsyncSession, item: StudyPlanItem, *, completed_at: datetime
) -> bool:
    """Complete only the exact item content snapshot the request inspected.

    The plan-row lock serializes this endpoint with first-party curriculum
    upgrades on Postgres. SQLite ignores ``FOR UPDATE``, so status plus
    ``updated_at`` form a portable compare-and-swap backstop: if an upgrade
    claimed or rewrote the row, this stale completion fails instead of granting
    completion credit to content the user did not see.
    """
    result = await db.exec(
        update(StudyPlanItem)
        .where(
            StudyPlanItem.id == item.id,
            StudyPlanItem.status == item.status,
            StudyPlanItem.updated_at == item.updated_at,
        )
        .values(
            status=ITEM_COMPLETE,
            completed_at=completed_at,
            updated_at=completed_at,
        )
        .execution_options(synchronize_session=False)
    )
    return result.rowcount == 1


@router.post("/study-plans/{plan_id}/items/{item_id}/complete", response_model=ItemDetail)
async def complete_item(
    plan_id: uuid.UUID,
    item_id: uuid.UUID,
    base_plan_revision: int | None = Query(default=None, ge=1),
    db: AsyncSession = Depends(get_session),
) -> ItemDetail:
    """Plan progress only. No card, score, session, or SM-2 field is read here."""
    graph = await _get_graph(db, plan_id, for_update=True)
    item = _available_item(graph, item_id)
    if base_plan_revision is not None:
        if graph.plan.revision != base_plan_revision:
            raise HTTPException(
                status_code=409,
                detail="plan has changed since this item was opened; reopen it and try again",
            )
    elif item.curriculum_key in await _fresh_completion_keys_for_plan(db, plan_id):
        raise HTTPException(
            status_code=409,
            detail=(
                "this curriculum item requires a current plan revision; "
                "reopen it and try again"
            ),
        )
    if item.status == ITEM_COMPLETE:
        raise HTTPException(status_code=409, detail="item is already complete")

    completed_at = _now()
    if not await _complete_item_cas(db, item, completed_at=completed_at):
        await db.rollback()
        raise HTTPException(
            status_code=409,
            detail="item changed while completion was being saved; reopen it and try again",
        )
    await db.commit()
    await db.refresh(item)
    return await _item_detail(db, graph, item)


# --- Practice Debrief -------------------------------------------------------


@router.get(
    "/study-plans/{plan_id}/items/{item_id}/practice-debrief",
    response_model=PracticeDebriefOut | None,
)
async def get_practice_debrief(
    plan_id: uuid.UUID,
    item_id: uuid.UUID,
    db: AsyncSession = Depends(get_session),
) -> PracticeDebriefOut | None:
    await _debrief_context(db, plan_id, item_id)
    row = await _debrief_for(db, item_id)
    return await _debrief_out(db, row) if row else None


@router.patch(
    "/study-plans/{plan_id}/items/{item_id}/practice-debrief/draft",
    response_model=PracticeDebriefOut,
)
async def save_practice_debrief_draft(
    plan_id: uuid.UUID,
    item_id: uuid.UUID,
    body: PracticeDebriefDraftIn,
    db: AsyncSession = Depends(get_session),
) -> PracticeDebriefOut:
    """Cheap, idempotent backup for the disk-first spoken-text draft."""
    plan, item = await _debrief_context(db, plan_id, item_id)
    if not _debrief_eligible(plan.subject_slug, item):
        raise HTTPException(status_code=409, detail="practice debrief is not available")

    for _attempt in range(3):
        now = _now()
        saved = await db.exec(
            update(StudyPlanPracticeDebrief)
            .where(
                StudyPlanPracticeDebrief.plan_id == plan_id,
                StudyPlanPracticeDebrief.plan_item_id == item_id,
                StudyPlanPracticeDebrief.submitted_at.is_(None),
            )
            .values(draft_text=body.text, updated_at=now)
            .execution_options(synchronize_session=False)
        )
        if saved.rowcount == 1:
            await db.commit()
            row = await _debrief_for(db, item_id)
            if row is None:  # pragma: no cover - protected by the item unique index
                raise RuntimeError("practice debrief draft disappeared")
            return _debrief_response(row)

        row = await _debrief_for(db, item_id)
        if row is not None and row.submitted_at is not None:
            raise HTTPException(status_code=409, detail="practice debrief is already submitted")
        if row is not None:
            await db.rollback()
            continue

        row = StudyPlanPracticeDebrief(
            plan_id=plan_id,
            plan_item_id=item_id,
            draft_text=body.text,
        )
        db.add(row)
        try:
            await db.commit()
        except IntegrityError:
            await db.rollback()
            continue
        return _debrief_response(row)

    raise HTTPException(status_code=409, detail="practice debrief changed; try again")


@router.post(
    "/study-plans/{plan_id}/items/{item_id}/practice-debrief",
    response_model=PracticeDebriefOut,
)
async def submit_practice_debrief(
    plan_id: uuid.UUID,
    item_id: uuid.UUID,
    body: PracticeDebriefSubmitIn,
    db: AsyncSession = Depends(get_session),
) -> PracticeDebriefOut:
    """Commit one unscored debrief. It never touches plan or review state.

    The conditional update is the immutability boundary for an existing draft:
    once one writer sets ``submitted_at``, every later writer observes rowcount
    zero and can only replay the exact text. The unique item index is the insert
    boundary when two first submissions race; the loser reloads the winner and
    applies the same replay rule.
    """
    if not body.text.strip():
        raise HTTPException(status_code=422, detail="debrief text is empty")
    plan, item = await _debrief_context(db, plan_id, item_id)
    if not _debrief_eligible(plan.subject_slug, item):
        raise HTTPException(status_code=409, detail="practice debrief is not available")

    for _attempt in range(3):
        now = _now()
        claimed = await db.exec(
            update(StudyPlanPracticeDebrief)
            .where(
                StudyPlanPracticeDebrief.plan_id == plan_id,
                StudyPlanPracticeDebrief.plan_item_id == item_id,
                StudyPlanPracticeDebrief.submitted_at.is_(None),
            )
            .values(
                draft_text=body.text,
                text=body.text,
                submitted_at=now,
                updated_at=now,
            )
            .execution_options(synchronize_session=False)
        )
        if claimed.rowcount == 1:
            await db.commit()
            submitted = await _debrief_for(db, item_id)
            if submitted is None:  # pragma: no cover - protected by the item unique index
                raise RuntimeError("submitted practice debrief disappeared")
            return await _debrief_out(db, submitted)

        row = await _debrief_for(db, item_id)
        if row is not None and row.submitted_at is not None:
            if row.text != body.text:
                raise HTTPException(
                    status_code=409,
                    detail="practice debrief is already submitted",
                )
            return await _debrief_out(db, row)

        if row is not None:
            # A concurrent draft insert became visible after our conditional
            # update. Retry so that submission still goes through the guarded
            # update instead of an unconditional ORM write.
            await db.rollback()
            continue

        db.add(
            StudyPlanPracticeDebrief(
                plan_id=plan_id,
                plan_item_id=item_id,
                draft_text=body.text,
                text=body.text,
                submitted_at=now,
            )
        )
        try:
            await db.commit()
        except IntegrityError:
            # Two first submissions can both observe no row. The item-unique
            # index elects the winner; after rollback the loser may only replay
            # identical text, or retry if the conflicting row was just a draft.
            await db.rollback()
            continue

        submitted = await _debrief_for(db, item_id)
        if submitted is None:  # pragma: no cover - commit made the row durable
            raise RuntimeError("submitted practice debrief disappeared")
        return await _debrief_out(db, submitted)

    winner = await _debrief_for(db, item_id)
    if winner is not None and winner.submitted_at is not None:
        if winner.text == body.text:
            return await _debrief_out(db, winner)
        raise HTTPException(status_code=409, detail="practice debrief is already submitted")
    raise HTTPException(status_code=409, detail="practice debrief changed; try again")


@router.post("/study-plans/{plan_id}/items/{item_id}/reopen/preview", response_model=ProposalOut)
async def preview_reopen(
    plan_id: uuid.UUID, item_id: uuid.UUID, db: AsyncSession = Depends(get_session)
) -> ProposalOut:
    """What reopening would do to the schedule. Writes nothing.

    Reopening changes Study Plan progress and nothing else: no card is deleted,
    no score changes, no SM-2 field rewinds. If it needs a replan, that replan is
    still a confirmation away.
    """
    graph = await _get_graph(db, plan_id)
    item = _available_item(graph, item_id)
    if item.status != ITEM_COMPLETE:
        raise HTTPException(status_code=409, detail="item is not complete")

    plan, weeks, items, deps = graph.sched_inputs()
    items = [
        replace(i, status=ITEM_PENDING) if i.id == str(item_id) else i
        for i in items
    ]
    proposal = sched.build_proposal(plan, weeks, items, deps)
    headline, text = _replan_copy(proposal)
    return _proposal_out(
        graph,
        proposal,
        kind="reopen",
        headline=headline
        if proposal.moves or proposal.unresolved
        else "Reopening this changes nothing else.",
        body=text,
        unchanged=[
            "Reopening changes Study Plan progress only.",
            *UNCHANGED_ALWAYS,
        ],
    )


@router.post("/study-plans/{plan_id}/items/{item_id}/reopen", response_model=ItemDetail)
async def reopen_item(
    plan_id: uuid.UUID,
    item_id: uuid.UUID,
    base_plan_revision: int = Query(...),
    db: AsyncSession = Depends(get_session),
) -> ItemDetail:
    graph = await _get_graph(db, plan_id, for_update=True)
    _require_current(graph.plan, base_plan_revision)
    item = _available_item(graph, item_id)
    if item.status != ITEM_COMPLETE:
        raise HTTPException(status_code=409, detail="item is not complete")

    before = sp.plan_snapshot(graph)
    plan_input, weeks, items, dependencies = graph.sched_inputs()
    items = [
        replace(candidate, status=ITEM_PENDING)
        if candidate.id == str(item_id)
        else candidate
        for candidate in items
    ]
    proposal = sched.build_proposal(plan_input, weeks, items, dependencies)
    if not proposal.valid:
        raise HTTPException(
            status_code=409, detail="reopening needs a replan; preview and apply it first"
        )

    await _claim_plan_mutation(
        db,
        graph.plan,
        expected_revision=base_plan_revision,
    )
    item.status = ITEM_PENDING
    item.reopened_at = _now()
    item.completed_at = None
    item.updated_at = item.reopened_at
    db.add(item)
    sp.record_revision(
        db,
        graph,
        kind=REVISION_REOPEN,
        before=before,
        after=sp.proposal_snapshot(proposal),
        summary=f"Reopened “{item.full_title}”",
    )
    await db.commit()
    return await _item_detail(db, graph, item)


@router.patch("/study-plans/{plan_id}/items/{item_id}", response_model=ItemDetail)
async def edit_item(
    plan_id: uuid.UUID,
    item_id: uuid.UUID,
    body: ItemEdit,
    db: AsyncSession = Depends(get_session),
) -> ItemDetail:
    graph = await _get_graph(db, plan_id, for_update=True)
    item = _available_item(graph, item_id)

    before = sp.plan_snapshot(graph)
    changed_estimate = (
        body.estimate_minutes is not None and body.estimate_minutes != item.estimate_minutes
    )
    base_revision: int | None = None
    if changed_estimate:
        base_revision = await _claim_plan_mutation(
            db,
            graph.plan,
            conflict_detail=(
                "plan changed while this item edit was being saved; reload and try again"
            ),
        )
    for field_name in (
        "full_title",
        "why_it_matters",
        "done_when",
        "notes",
        "study_block_label",
        "study_block_weekday",
        "study_block_minute_of_day",
        "study_block_reminder_on",
    ):
        value = getattr(body, field_name)
        if value is not None:
            setattr(item, field_name, value)
    if body.estimate_minutes is not None:
        item.estimate_minutes = body.estimate_minutes
        item.estimate_source = "user_edited"
        item.estimate_confidence = "high"
    item.updated_at = _now()
    db.add(item)

    if changed_estimate:
        # An estimate edit can displace work, so it is a material change and gets
        # a revision. The proposal is not applied here. If it no longer fits, the
        # week reads as over capacity and the app offers the replan.
        assert base_revision is not None
        proposal = sp.build_proposal_for(graph)
        sp.record_revision(
            db,
            graph,
            kind=REVISION_ITEM_EDIT,
            before=before,
            after=sp.proposal_snapshot(proposal),
            summary=f"Estimate changed on “{item.full_title}”",
        )

    await db.commit()
    return await _item_detail(db, graph, item)


# --- lifecycle --------------------------------------------------------------


@router.post("/study-plans/{plan_id}/pause", response_model=PlanOverview)
async def pause_plan(plan_id: uuid.UUID, db: AsyncSession = Depends(get_session)) -> PlanOverview:
    graph = await _get_graph(db, plan_id, for_update=True)
    if graph.plan.status != PLAN_ACTIVE:
        raise HTTPException(status_code=409, detail="plan is not active")
    sp.pause(db, graph)
    await db.commit()
    return await _overview(db, plan_id)


@router.post("/study-plans/{plan_id}/resume/preview", response_model=ProposalOut)
async def preview_resume(
    plan_id: uuid.UUID, db: AsyncSession = Depends(get_session)
) -> ProposalOut:
    """What resuming would cost. A short pause usually costs nothing.

    A flexible plan paused past its schedule previews shifted weeks; a fixed plan
    keeps its deadline and shows the recovery options instead.
    """
    graph = await _get_graph(db, plan_id)
    if graph.plan.status not in (PLAN_PAUSED, PLAN_ARCHIVED, PLAN_COMPLETED):
        raise HTTPException(status_code=409, detail="plan is already active")

    proposal = sp.build_proposal_for(graph)
    if graph.plan.mode == MODE_FIXED and not sp.deadline_fits(
        graph.plan, proposal.forecast_end_plan_week
    ):
        headline = "The deadline no longer fits."
        text = (
            "The deadline does not move. Reduce scope or raise capacity to close "
            "the gap before resuming."
        )
    elif proposal.moves:
        headline, text = _replan_copy(proposal)
    else:
        headline = "Nothing needs to move."
        text = "The pause was short enough that the schedule still fits."
    return _proposal_out(
        graph,
        proposal,
        kind="resume",
        headline=headline,
        body=text,
        unchanged=UNCHANGED_ALWAYS,
    )


@router.post("/study-plans/{plan_id}/resume/apply", response_model=PlanOverview)
async def apply_resume(
    plan_id: uuid.UUID,
    base_plan_revision: int = Query(...),
    db: AsyncSession = Depends(get_session),
) -> PlanOverview:
    graph = await _get_graph(db, plan_id, for_update=True)
    _require_current(graph.plan, base_plan_revision)
    if graph.plan.status == PLAN_ACTIVE:
        raise HTTPException(status_code=409, detail="plan is already active")

    proposal = sp.build_proposal_for(graph)
    if not proposal.valid:
        raise HTTPException(
            status_code=409, detail="resuming needs a replan; preview and apply it first"
        )

    base_revision = await _claim_plan_mutation(
        db,
        graph.plan,
        expected_revision=base_plan_revision,
    )
    await sp.make_active(
        db,
        graph,
        base_plan_revision=base_revision,
        kind=REVISION_RESUME,
        summary="Plan resumed",
    )
    await _commit_activation(db)
    return await _overview(db, plan_id)


@router.post("/study-plans/{plan_id}/activate", response_model=PlanOverview)
async def activate_plan(
    plan_id: uuid.UUID,
    base_plan_revision: int = Query(...),
    db: AsyncSession = Depends(get_session),
) -> PlanOverview:
    graph = await _get_graph(db, plan_id, for_update=True)
    _require_current(graph.plan, base_plan_revision)
    claimed_revision = await _claim_plan_mutation(
        db,
        graph.plan,
        expected_revision=base_plan_revision,
    )
    await sp.make_active(
        db,
        graph,
        base_plan_revision=claimed_revision,
        kind=REVISION_ACTIVATE,
        summary="Made this the active plan",
    )
    await _commit_activation(db)
    return await _overview(db, plan_id)


@router.post("/study-plans/{plan_id}/complete", response_model=PlanOverview)
async def complete_plan(
    plan_id: uuid.UUID, db: AsyncSession = Depends(get_session)
) -> PlanOverview:
    graph = await _get_graph(db, plan_id, for_update=True)
    sp.complete(db, graph)
    await db.commit()
    return await _overview(db, plan_id)


@router.post("/study-plans/{plan_id}/archive", response_model=PlanOverview)
async def archive_plan(plan_id: uuid.UUID, db: AsyncSession = Depends(get_session)) -> PlanOverview:
    graph = await _get_graph(db, plan_id, for_update=True)
    sp.archive(db, graph)
    await db.commit()
    return await _overview(db, plan_id)


@router.post("/study-plans/{plan_id}/duplicate", response_model=PlanOverview, status_code=201)
async def duplicate(plan_id: uuid.UUID, db: AsyncSession = Depends(get_session)) -> PlanOverview:
    """Copy the plan's structure and reset its progress. Creates it paused."""
    guide_characters = (
        await db.exec(
            select(func.length(StudyPlan.guide_text)).where(
                StudyPlan.id == plan_id,
                StudyPlan.user_id == current_user_id(),
            )
        )
    ).first()
    if guide_characters is None:
        raise HTTPException(status_code=404, detail="plan not found")
    await storage.reserve_study_plan(
        db, user_id=current_user_id(), characters=guide_characters
    )
    # Reload under the plan lock after taking the account boundary. Account
    # deletion and plan edits now have a single lock order and the copy cannot
    # mix rows from two revisions.
    graph = await _get_graph(db, plan_id, for_update=True)
    rows = sp.duplicate_plan(
        graph.plan, graph, start_date=spi.default_start_date(await local_today(db))
    )
    await sp.insert_plan_rows(db, rows["plan"], rows)
    db.add(StudyPlanDuplication(source_plan_id=graph.plan.id, duplicated_plan_id=rows["plan"].id))
    await db.commit()
    return await _overview(db, rows["plan"].id)


# --- card proposals ---------------------------------------------------------


def _proposal_response(p: StudyPlanCardProposal, existing_context: str = "") -> CardProposalOut:
    failed = sp.first_failed_gate(p.gate_results)
    return CardProposalOut(
        id=p.id,
        revision=p.revision,
        topic=p.topic,
        category=p.category,
        canonical_question=p.canonical_question,
        reason=p.reason,
        gate=[
            {
                "question_index": g["question_index"],
                "question": g["question"],
                "passed": g["passed"],
                "reason": g["reason"],
            }
            for g in p.gate_results
        ],
        disposition=p.disposition,
        duplicate_check_result=p.duplicate_check_result,
        existing_card_id=p.duplicate_card_id,
        existing_card_context=existing_context,
        failed_question_index=failed["question_index"] if failed else None,
        failed_reason=failed["reason"] if failed else "",
    )


async def _card_proposal_list(
    db: AsyncSession,
    *,
    plan_id: uuid.UUID,
    item_id: uuid.UUID,
    proposals: list[StudyPlanCardProposal],
    active_contract: int,
) -> CardProposalList:
    """Render durable proposals without crossing another paid-call boundary."""
    rows: list[CardProposalOut] = []
    for proposal in proposals:
        context = ""
        if proposal.duplicate_card_id:
            card = (
                await db.exec(
                    select(Card).where(
                        col(Card.id) == proposal.duplicate_card_id,
                        Card.user_id == current_user_id(),
                    )
                )
            ).first()
            if card is not None:
                if active_contract == SCORING_CONTRACT_V2:
                    score = (
                        card.last_accuracy
                        if card.last_accuracy is not None
                        else "unrated"
                    )
                    label = "recall"
                else:
                    score = card.last_score if card.last_score is not None else "unrated"
                    label = "score"
                context = (
                    f"Existing · {label} {score} · due {card.next_review_at.isoformat()}"
                )
        rows.append(_proposal_response(proposal, context))

    suggested = sum(1 for row in rows if row.disposition == DISPOSITION_SUGGESTED)
    return CardProposalList(
        plan_id=plan_id,
        item_id=item_id,
        supports_recall_cards=True,
        suggested_count=suggested,
        proposals=rows,
        note=CARD_BOUNDARY_NOTE,
    )


@router.post(
    "/study-plans/{plan_id}/items/{item_id}/card-proposals",
    response_model=CardProposalList,
)
async def create_card_proposals(
    plan_id: uuid.UUID, item_id: uuid.UUID, db: AsyncSession = Depends(get_session)
) -> CardProposalList:
    """Generate candidates for a completed item. Creates proposals, never cards.

    Gated three ways before the model is even called: the subject must be one the
    technical rubric can grade, the item must be complete, and the item must not
    already have proposals.
    """
    graph = await _get_graph(db, plan_id)
    item = _available_item(graph, item_id)
    if item.status != ITEM_COMPLETE:
        raise HTTPException(status_code=409, detail="item is not complete")

    supported = sp.slug_supports_cards(graph.plan.subject_slug)
    if not supported:
        return CardProposalList(
            plan_id=plan_id,
            item_id=item_id,
            supports_recall_cards=False,
            suggested_count=0,
            proposals=[],
            note=(
                "This subject uses plan-local retrieval activities. Unprompted recall "
                "cards are only proposed for subjects the scoring rubric can grade."
            ),
        )

    if not _source_recall_eligible(graph.plan.subject_slug, item):
        return CardProposalList(
            plan_id=plan_id,
            item_id=item_id,
            supports_recall_cards=True,
            suggested_count=0,
            proposals=[],
            note=(
                "This item has no confirmed answer basis, so it cannot produce "
                "a review card."
            ),
        )

    observed_gap = ""
    if item.type == ITEM_PRACTICE:
        # A reflection identifies where to look; the trusted source still supplies
        # the answer basis. Without both, no Practice proposal call is allowed.
        debrief = await _debrief_for(db, item_id)
        if debrief is None or debrief.submitted_at is None:
            raise HTTPException(status_code=409, detail="practice debrief is required")
        observed_gap = debrief.text

    active_contract = active_scoring_contract_version()
    existing = (
        await db.exec(
            select(StudyPlanCardProposal).where(
                col(StudyPlanCardProposal.source_plan_item_id) == item_id
            )
        )
    ).all()
    if not existing:
        weak_score = (
            Card.last_accuracy if active_contract == SCORING_CONTRACT_V2 else Card.last_score
        )
        weak = (
            await db.exec(
                select(Card)
                .where(Card.user_id == current_user_id(), active_card_filter())
                .where(col(weak_score).is_not(None))
                .where(col(weak_score) <= 3)
                .order_by(col(weak_score))
                .limit(10)
            )
        ).all()
        await usage.ensure_available(db, current_user_id(), "card_proposal", get_settings())
        # Paid generation is serialized at the account boundary. A concurrent
        # request may have committed this item's proposal set while we waited,
        # so refresh the idempotency condition before any transmission.
        existing = (
            await db.exec(
                select(StudyPlanCardProposal).where(
                    col(StudyPlanCardProposal.source_plan_item_id) == item_id
                )
            )
        ).all()
        if existing:
            return await _card_proposal_list(
                db,
                plan_id=plan_id,
                item_id=item_id,
                proposals=existing,
                active_contract=active_contract,
            )
        candidates = await llm.propose_cards(
            subject=graph.plan.subject,
            item_title=item.full_title,
            why_it_matters=item.why_it_matters,
            done_when=item.done_when,
            source_excerpt=item.source_excerpt,
            observed_gap=observed_gap,
            existing_weak_topics=[c.topic for c in weak],
        )
        usage.record(db, current_user_id(), "card_proposal")
        existing_cards = await sp.normalized_card_index(db, current_user_id())
        for candidate in candidates[: sp.MAX_SUGGESTED_CARDS]:
            topic = str(candidate.get("topic", "")).strip()
            if not topic:
                continue
            gate = sp.normalise_gate_results(candidate.get("gate", []))
            rubric = clean_rubric(candidate.get("answer_rubric"))
            question = str(candidate.get("canonical_question", "")).strip()
            grounding = Grounding(
                source_label=f"{item.full_title} · Study Plan",
                answer_basis=item.source_excerpt,
                answer_rubric=storage_rubric(rubric),
                canonical_question=question,
            )
            try:
                grounding.require_complete()
            except GroundingError as exc:
                # Structured output should make this unreachable, but an
                # incomplete authority must fail the existing gate closed rather
                # than becoming a selectable malformed proposal.
                gate[0] = {
                    **gate[0],
                    "passed": False,
                    "reason": f"Grounding is incomplete: {', '.join(exc.missing)}.",
                }
            exact = existing_cards.get(sp.normalize_topic(topic))
            duplicate_result, card_id = sp.classify_duplicate(exact, None)
            row = StudyPlanCardProposal(
                plan_id=plan_id,
                source_plan_item_id=item_id,
                topic=topic,
                category=str(candidate.get("category", "Unsorted")).strip() or "Unsorted",
                canonical_question=question,
                source_label=grounding.source_label,
                answer_basis=grounding.answer_basis,
                answer_rubric=rubric,
                reason=str(candidate.get("reason", "")).strip(),
                gate_results=gate,
                duplicate_check_result=duplicate_result,
                duplicate_card_id=card_id,
                disposition=sp.disposition_for(gate, duplicate_result),
                normalized_topic=sp.normalize_topic(topic),
            )
            db.add(row)
            existing.append(row)
        await db.commit()

    return await _card_proposal_list(
        db,
        plan_id=plan_id,
        item_id=item_id,
        proposals=existing,
        active_contract=active_contract,
    )


@router.get(
    "/study-plans/{plan_id}/items/{item_id}/card-proposals",
    response_model=CardProposalList,
)
async def list_card_proposals(
    plan_id: uuid.UUID, item_id: uuid.UUID, db: AsyncSession = Depends(get_session)
) -> CardProposalList:
    """Read stored proposals. Never calls the model."""
    graph = await _get_graph(db, plan_id)
    _available_item(graph, item_id)
    plan = graph.plan
    rows = (
        await db.exec(
            select(StudyPlanCardProposal).where(
                col(StudyPlanCardProposal.source_plan_item_id) == item_id
            )
        )
    ).all()
    out = [_proposal_response(p) for p in rows]
    return CardProposalList(
        plan_id=plan_id,
        item_id=item_id,
        supports_recall_cards=sp.slug_supports_cards(plan.subject_slug),
        suggested_count=sum(1 for r in out if r.disposition == DISPOSITION_SUGGESTED),
        proposals=out,
        note=CARD_BOUNDARY_NOTE,
    )


@router.post(
    "/study-plans/{plan_id}/card-proposals/accept",
    response_model=CardAcceptOut,
)
async def accept_card_proposals(
    plan_id: uuid.UUID, body: CardAcceptIn, db: AsyncSession = Depends(get_session)
) -> CardAcceptOut:
    """Create the selected cards atomically and idempotently.

    The whole operation is one transaction. Either every selected card and the
    acceptance record commit together, or nothing does. A half-created batch
    that reported success would leave the user unable to tell which cards exist.

    The exact normalized-topic check runs **again here**, inside the transaction,
    because a card can be written by hand between Preview and Accept. If one
    appeared, the batch aborts and that candidate refreshes as EXISTING rather
    than the rest being created while claiming atomic success.
    """
    user_id = current_user_id()
    graph = await _get_graph(db, plan_id)
    plan = graph.plan
    today = await local_today(db)

    computed_hash = sp.request_hash(body.selected_proposal_ids, body.edits)

    async def replay_or_clear_prior() -> CardAcceptOut | None:
        prior = (
            await db.exec(
                select(StudyPlanCardProposalAcceptance).where(
                    col(StudyPlanCardProposalAcceptance.plan_id) == plan_id,
                    col(StudyPlanCardProposalAcceptance.idempotency_key)
                    == body.idempotency_key,
                )
            )
        ).first()
        if prior is None:
            return None
        if prior.request_hash != computed_hash:
            # Same key, different intent. This is a client bug, not a replay, and
            # silently honouring either request would create the wrong cards.
            raise HTTPException(
                status_code=409, detail="idempotency key reused with a different request"
            )
        if prior.status == ACCEPTANCE_COMMITTED:
            prior_proposal = (
                await db.exec(
                    select(StudyPlanCardProposal).where(
                        StudyPlanCardProposal.id == prior.proposal_id
                    )
                )
            ).first()
            if prior_proposal is None:
                raise HTTPException(status_code=404, detail="proposal not found")
            _available_item(graph, prior_proposal.source_plan_item_id)
            return CardAcceptOut(
                status="committed",
                created_card_ids=[uuid.UUID(i) for i in prior.created_card_ids],
                replayed=True,
            )
        # A previous attempt rolled back. Nothing was created, so it is safe to
        # run again under the same key.
        await db.delete(prior)
        await db.flush()
        return None

    replay = await replay_or_clear_prior()
    if replay is not None:
        return replay

    proposal_rows = (
        await db.exec(
            select(StudyPlanCardProposal).where(
                col(StudyPlanCardProposal.id).in_(body.selected_proposal_ids)
            )
        )
    ).all()
    proposals_by_id = {row.id: row for row in proposal_rows}
    proposals = []
    for proposal_id in body.selected_proposal_ids:
        row = proposals_by_id.get(proposal_id)
        if row is None:
            raise HTTPException(status_code=404, detail="proposal not found")
        if row.plan_id != plan_id:
            raise HTTPException(status_code=404, detail="proposal not found")
        _available_item(graph, row.source_plan_item_id)
        if row.revision != body.proposal_revision:
            raise HTTPException(status_code=409, detail="proposal has been edited")
        if not sp.selectable(row):
            raise HTTPException(status_code=409, detail="proposal is not selectable")
        proposals.append(row)

    # The duplicate re-check runs over the whole batch *before* anything is
    # staged. Doing it inline while creating cards would mean unwinding a
    # half-built batch, and an ORM rollback mid-request expires exactly the rows
    # needed to report which candidate clashed. Checking first means the abort
    # path never has anything to undo.
    prepared: list[tuple[StudyPlanCardProposal, Card]] = []
    await lock_topic_creation(db, user_id)
    # A concurrent retry may have committed while this request waited for the
    # per-account topic-creation lock. Re-read under that lock so same-key
    # requests replay the committed batch instead of misreporting its new card
    # as an unrelated duplicate.
    replay = await replay_or_clear_prior()
    if replay is not None:
        return replay
    existing_cards = await sp.normalized_card_index(db, user_id)
    prepared_topics: set[str] = set()
    for row in proposals:
        edit = body.edits.get(str(row.id), {})
        topic = (edit.get("topic") or row.topic).strip()
        question = (edit.get("canonical_question") or row.canonical_question).strip()

        normalized_topic = sp.normalize_topic(topic)
        if not normalized_topic or normalized_topic in prepared_topics:
            raise HTTPException(
                status_code=409,
                detail="selected proposals must resolve to distinct non-empty topics",
            )
        prepared_topics.add(normalized_topic)

        clash = existing_cards.get(normalized_topic)
        if clash is not None:
            row.duplicate_check_result = DUPLICATE_EXACT
            row.duplicate_card_id = clash.id
            row.disposition = DISPOSITION_EXISTING
            row.updated_at = _now()
            db.add(row)
            await db.commit()
            raise HTTPException(
                status_code=409,
                detail=f"a card for “{clash.topic}” already exists; nothing was created",
            )
        try:
            card = build_grounded_card(
                user_id=user_id,
                topic=topic,
                category=row.category,
                grounding=Grounding(
                    source_label=row.source_label,
                    answer_basis=row.answer_basis,
                    answer_rubric=row.answer_rubric,
                    canonical_question=question,
                ),
                today=today,
                schedule="now",
            )
        except GroundingError as exc:
            raise HTTPException(
                status_code=409,
                detail={"code": "missing_grounding", "missing": exc.missing},
            ) from exc
        prepared.append((row, card))

    acceptance = StudyPlanCardProposalAcceptance(
        plan_id=plan.id,
        proposal_id=proposals[0].id,
        idempotency_key=body.idempotency_key,
        request_hash=computed_hash,
        proposal_revision=body.proposal_revision,
        status=ACCEPTANCE_PROCESSING,
    )
    db.add(acceptance)

    created: list[uuid.UUID] = []
    links: list[StudyPlanCardLink] = []
    for row, card in prepared:
        db.add(card)
        links.append(
            StudyPlanCardLink(
                plan_id=plan.id,
                plan_item_id=row.source_plan_item_id,
                card_id=card.id,
                acceptance_id=acceptance.id,
            )
        )
        row.disposition = DISPOSITION_ACCEPTED
        row.updated_at = _now()
        db.add(row)
        created.append(card.id)

    # The links reference both the cards and the acceptance, and the unit of work
    # would otherwise emit them first; see `sp.insert_plan_rows` for why ordering
    # is manual here. Still one transaction: a flush is not a commit.
    await db.flush()
    for link in links:
        db.add(link)

    acceptance.status = ACCEPTANCE_COMMITTED
    acceptance.created_card_ids = [str(i) for i in created]
    acceptance.committed_at = _now()
    acceptance.updated_at = acceptance.committed_at
    db.add(acceptance)

    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        acceptance.status = ACCEPTANCE_FAILED
        raise HTTPException(status_code=409, detail="nothing was created; try again") from exc

    # Card ids are returned only after commit, so ADDED can only render on cards
    # that exist.
    return CardAcceptOut(status="committed", created_card_ids=created, replayed=False)


@router.post("/study-plans/{plan_id}/card-proposals/resolve-duplicate", status_code=204)
async def resolve_duplicate(
    plan_id: uuid.UUID, body: DuplicateResolution, db: AsyncSession = Depends(get_session)
) -> Response:
    """Record the user's call on a possible overlap. Never decides it for them."""
    graph = await _get_graph(db, plan_id)
    row = (
        await db.exec(
            select(StudyPlanCardProposal).where(col(StudyPlanCardProposal.id) == body.proposal_id)
        )
    ).first()
    if row is None or row.plan_id != plan_id:
        raise HTTPException(status_code=404, detail="proposal not found")
    _available_item(graph, row.source_plan_item_id)

    if body.action == "keep_new":
        row.duplicate_check_result = "none"
        row.duplicate_card_id = None
        row.disposition = sp.disposition_for(row.gate_results, "none")
    elif body.action == "use_existing":
        row.disposition = DISPOSITION_EXISTING
    else:
        row.disposition = DISPOSITION_SKIPPED
    row.updated_at = _now()
    db.add(row)
    await db.commit()
    return Response(status_code=204)
