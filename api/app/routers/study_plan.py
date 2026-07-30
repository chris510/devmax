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

import uuid
from datetime import UTC, date, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy.exc import IntegrityError
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.db import get_session
from app.models import (
    ACCEPTANCE_COMMITTED,
    ACCEPTANCE_FAILED,
    ACCEPTANCE_PROCESSING,
    DELIVERY_CONVERSATIONAL,
    DISPOSITION_ACCEPTED,
    DISPOSITION_EXISTING,
    DISPOSITION_SKIPPED,
    DISPOSITION_SUGGESTED,
    DRAFT_FAILED,
    DRAFT_READY,
    DUPLICATE_EXACT,
    ITEM_COMPLETE,
    ITEM_DEFERRED,
    ITEM_LEARN,
    ITEM_PENDING,
    ITEM_PRACTICE,
    ITEM_RETRIEVE,
    MODE_FIXED,
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
    StudyPlan,
    StudyPlanCardLink,
    StudyPlanCardProposal,
    StudyPlanCardProposalAcceptance,
    StudyPlanDuplication,
    StudyPlanGuideDraft,
    StudyPlanItem,
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
from app.services import llm
from app.services import study_plan as sp
from app.services import study_plan_import as spi
from app.services import study_plan_scheduler as sched

router = APIRouter(tags=["study-plan"])

WEEKDAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")

# Stated on every card-acceptance screen, in both directions: these cards join
# the review schedule, and their reviews never come back and change this plan.
CARD_BOUNDARY_NOTE = (
    "These cards join Today's review schedule. Their reviews don't change this "
    "Study Plan."
)


def _now() -> datetime:
    return datetime.now(UTC)


# --- loading ----------------------------------------------------------------


async def _get_plan(db: AsyncSession, plan_id: uuid.UUID) -> StudyPlan:
    plan = (await db.exec(select(StudyPlan).where(col(StudyPlan.id) == plan_id))).first()
    if plan is None:
        raise HTTPException(status_code=404, detail="plan not found")
    return plan


async def _get_graph(db: AsyncSession, plan_id: uuid.UUID) -> sp.PlanGraph:
    return await sp.load_plan_graph(db, await _get_plan(db, plan_id))


async def _commit_activation(db: AsyncSession) -> None:
    """Commit a transaction that made a plan active.

    The partial unique index is the last line of defence against two active
    plans; a concurrent activation lands here rather than corrupting the state.
    """
    try:
        await db.commit()
    except IntegrityError as exc:  # pragma: no cover — the partial index backstop
        await db.rollback()
        raise HTTPException(status_code=409, detail="another plan is already active") from exc


def _require_current(plan: StudyPlan, base_plan_revision: int) -> None:
    """A proposal computed against an older plan is refused, not merged.

    409 rather than 422: the request was well-formed, the world moved. The client
    regenerates the proposal and shows the user the new arithmetic.
    """
    if plan.revision != base_plan_revision:
        raise HTTPException(status_code=409, detail="plan has changed since this proposal")


def _forecast_label(plan: StudyPlan, week: int) -> str:
    return sched.forecast_label(plan.start_date, week)


def _block_label(item: StudyPlanItem) -> str | None:
    if item.study_block_weekday is None or item.study_block_minute_of_day is None:
        return None
    hour, minute = divmod(item.study_block_minute_of_day, 60)
    return f"{WEEKDAYS[item.study_block_weekday - 1]} {hour:02d}:{minute:02d}"


async def _insert_plan_rows(db: AsyncSession, plan: StudyPlan, rows: dict[str, Any]) -> None:
    """Insert a whole plan graph, flushing between levels.

    The flushes are load-bearing. These models declare foreign keys but no
    SQLAlchemy `relationship()`, and the unit of work orders inserts across
    mappers from relationships rather than from table-level constraints — so
    without this it emits `study_plan_items` before `study_plan_phases` (they
    sort alphabetically) and Postgres rejects the batch on a foreign key. SQLite
    accepts it, because it does not enforce foreign keys by default, which is
    exactly the class of bug the Postgres test run exists to catch.
    """
    db.add(plan)
    await db.flush()
    for key in ("phases", "weeks", "items"):
        for row in rows[key]:
            db.add(row)
        await db.flush()
    # Dependency edges point at items, so they go last. Self-references need no
    # second pass — `source_item_id` is already set on the rows above.
    for row in rows["dependencies"]:
        db.add(row)
    await db.flush()


# --- Today ------------------------------------------------------------------


@router.get("/study-plans/active/summary", response_model=ActivePlanSummary)
async def active_summary(db: AsyncSession = Depends(get_session)) -> ActivePlanSummary:
    """Today's plan line. Zero active plans is a normal 200, not a 404.

    Declared before `/study-plans/{plan_id}` so `active` is never read as an id.
    """
    plan = await sp.active_plan(db)
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
            await db.exec(
                select(StudyPlanPhase).where(col(StudyPlanPhase.id) == current.phase_id)
            )
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
    checks = [CheckRow(**{k: v for k, v in c.items() if k != "item_keys"})
              for c in (draft.checks or [])]
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


async def _run_import(db: AsyncSession, draft: StudyPlanGuideDraft) -> None:
    """Import, validate, and store — or store the failure with the guide intact.

    A failed import never raises past this point. The draft keeps the guide text,
    the duration, the capacity, the mode, the deadline, and every edit, so Retry
    is one tap and loses nothing the user typed.
    """
    try:
        raw = await llm.import_guide(
            guide_text=draft.guide_text,
            requested_weeks=draft.requested_weeks,
            weekly_capacity_minutes=draft.weekly_capacity_minutes,
            mode=draft.mode,
            deadline=draft.deadline.isoformat() if draft.deadline else None,
            subject_hint=draft.subject_hint,
            title_hint=draft.title_hint,
        )
    except llm.LLMError as exc:
        draft.status = DRAFT_FAILED
        draft.error = str(exc)
        draft.updated_at = _now()
        db.add(draft)
        return

    draft.raw_response = raw
    _revalidate(draft)


def _revalidate(draft: StudyPlanGuideDraft) -> None:
    """Re-run the gate over the stored response plus the user's resolutions."""
    resolutions = dict((draft.preview or {}).get("resolutions", {}))
    try:
        result = spi.validate_import(
            draft.raw_response,
            guide_text=draft.guide_text,
            requested_weeks=draft.requested_weeks,
            weekly_capacity_minutes=draft.weekly_capacity_minutes,
            mode=draft.mode,
            deadline=draft.deadline,
            start_date=draft.start_date,
            resolutions=resolutions,
        )
    except spi.ImportError_ as exc:
        draft.status = DRAFT_FAILED
        draft.error = str(exc)
        draft.updated_at = _now()
        return

    draft.preview = result.preview
    draft.checks = [c.as_dict() for c in result.checks]
    draft.status = DRAFT_READY
    draft.error = ""
    draft.updated_at = _now()


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

    draft = StudyPlanGuideDraft(
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

    await _run_import(db, draft)
    db.add(draft)
    await db.commit()
    await db.refresh(draft)
    return _preview_response(draft)


async def _get_draft(db: AsyncSession, draft_id: uuid.UUID) -> StudyPlanGuideDraft:
    draft = (
        await db.exec(
            select(StudyPlanGuideDraft).where(col(StudyPlanGuideDraft.id) == draft_id)
        )
    ).first()
    if draft is None:
        raise HTTPException(status_code=404, detail="draft not found")
    return draft


@router.post("/study-plans/preview/{draft_id}/retry", response_model=PreviewOut)
async def retry_preview(
    draft_id: uuid.UUID, db: AsyncSession = Depends(get_session)
) -> PreviewOut:
    draft = await _get_draft(db, draft_id)
    await _run_import(db, draft)
    db.add(draft)
    await db.commit()
    await db.refresh(draft)
    return _preview_response(draft)


@router.patch("/study-plans/preview/{draft_id}", response_model=PreviewOut)
async def edit_preview(
    draft_id: uuid.UUID, body: PreviewEdit, db: AsyncSession = Depends(get_session)
) -> PreviewOut:
    """Apply edits and review decisions, then re-run the gate over them.

    Edits are applied to the stored raw response rather than the preview, so a
    later re-validation sees them too — an edit that only lived on the preview
    would silently revert on the next check.
    """
    draft = await _get_draft(db, draft_id)
    if not draft.raw_response:
        raise HTTPException(status_code=409, detail="draft has no import to edit")

    raw = dict(draft.raw_response)
    if body.item_estimates:
        items = [dict(i) for i in raw.get("items", [])]
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
    for field_name in (
        "estimates_reviewed",
        "retrieval_approved",
        "retrieval_rejected",
        "dependencies_confirmed",
    ):
        value = getattr(body, field_name)
        if value is not None:
            existing = set(resolutions.get(field_name, []))
            resolutions[field_name] = sorted(existing | set(value))
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
async def create_plan(
    body: PlanCreate, db: AsyncSession = Depends(get_session)
) -> PlanOverview:
    """Create a plan from a resolved preview. One transaction, no model call."""
    draft = await _get_draft(db, body.draft_id)
    if draft.status != DRAFT_READY:
        raise HTTPException(status_code=409, detail="draft is not ready")
    if not all(c.get("status") == "ok" for c in (draft.checks or [])):
        raise HTTPException(status_code=409, detail="draft has unresolved checks")

    rows = spi.build_plan_rows(draft.preview, start_date=draft.start_date)
    plan: StudyPlan = rows["plan"]

    previous_active = await sp.active_plan(db) if body.activate else None
    if body.activate:
        plan.status = PLAN_ACTIVE
        if previous_active is not None:
            # Making another plan active pauses the current one. The client has
            # already confirmed this; the database's partial unique index is what
            # guarantees the two never overlap — and it is evaluated per statement,
            # so the pause has to reach the database before the activation does.
            previous_active.status = PLAN_PAUSED
            previous_active.paused_at = _now()
            previous_active.updated_at = previous_active.paused_at
            db.add(previous_active)
            await db.flush()
    else:
        plan.status = PLAN_PAUSED
        plan.paused_at = _now()

    await _insert_plan_rows(db, plan, rows)
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
        await db.exec(select(StudyPlan).order_by(col(StudyPlan.created_at).desc()))
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
    all_weeks = (await db.exec(select(StudyPlanWeek))).all()
    all_items = (await db.exec(select(StudyPlanItem))).all()
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
                f"Paused {when} · Week {plan.current_week_index} "
                f"of {week_counts.get(plan.id, 0)}"
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
            items = graph.items_in(week)
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
                current_week_label=(
                    f"Week {plan.current_week_index}" if is_current else None
                ),
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
async def get_overview(
    plan_id: uuid.UUID, db: AsyncSession = Depends(get_session)
) -> PlanOverview:
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
    workload = sched.summarise_week(
        sp.to_sched_items(items, graph.weeks, graph.phases)
    )
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
        section = sched.summarise_week(
            sp.to_sched_items(rows_in, graph.weeks, graph.phases)
        )
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
        capacity_line=(
            f"{sched.format_hours(planned)} of {sched.format_hours(capacity)} planned"
        ),
        sections=sections,
    )


async def _item_detail(db: AsyncSession, graph: sp.PlanGraph, item: StudyPlanItem) -> ItemDetail:
    week = graph.week_by_id.get(item.week_id)
    phase = graph.phase_by_id.get(item.phase_id)
    links = (
        await db.exec(
            select(StudyPlanCardLink).where(col(StudyPlanCardLink.plan_item_id) == item.id)
        )
    ).all()
    return ItemDetail(
        id=item.id,
        plan_id=item.plan_id,
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
            and sp.slug_supports_cards(graph.plan.subject_slug)
        ),
        blocked_by=[b.full_title for b in sp.blocking_dependencies(graph, item)],
    )


@router.get("/study-plans/{plan_id}/items/{item_id}", response_model=ItemDetail)
async def get_item(
    plan_id: uuid.UUID, item_id: uuid.UUID, db: AsyncSession = Depends(get_session)
) -> ItemDetail:
    graph = await _get_graph(db, plan_id)
    item = graph.item_by_id.get(item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="item not found")
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
async def get_recap(
    plan_id: uuid.UUID, db: AsyncSession = Depends(get_session)
) -> PlanRecap:
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
            1
            for i in graph.items
            if i.priority == PRIORITY_OPTIONAL and i.status == ITEM_DEFERRED
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
            for i in graph.items
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
            UnresolvedOut(
                title=titles.get(u.item_id, ""), minutes=u.minutes, reason=u.reason
            )
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
    "Your cards, scores, session history, mastery summaries, and review schedule "
    "are untouched.",
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
    graph = await _get_graph(db, plan_id)
    _require_current(graph.plan, body.base_plan_revision)
    kwargs = _replan_kwargs(body)
    proposal = sp.build_proposal_for(graph, **kwargs)
    if not proposal.valid:
        raise HTTPException(status_code=409, detail="proposal does not validate")

    headline, text = _replan_copy(proposal)
    for item_id in body.deferred_item_ids:
        item = graph.item_by_id.get(item_id)
        if item is not None:
            item.status = ITEM_DEFERRED
            item.updated_at = _now()
            db.add(item)

    sp.apply_proposal(
        db,
        graph,
        proposal,
        kind=REVISION_REPLAN,
        summary=headline,
        capacity_overrides=kwargs["capacity_overrides"],
        default_capacity_minutes=kwargs["default_capacity_minutes"],
    )
    await db.commit()
    return _proposal_out(
        graph, proposal, kind="replan", headline=headline, body=text,
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
    validity and writes nothing — the user confirms it through the replan path,
    which is the same code with an explicit confirmation in front of it.
    """
    graph = await _get_graph(db, plan_id)
    _require_current(graph.plan, body.base_plan_revision)
    if index not in graph.week_by_index:
        raise HTTPException(status_code=404, detail="week not found")

    overrides = {index: body.override_capacity_minutes}
    proposal = sp.build_proposal_for(graph, capacity_overrides=overrides)
    headline, text = _replan_copy(proposal)

    if proposal.valid and not proposal.moves:
        effective = (
            body.override_capacity_minutes or graph.plan.default_weekly_capacity_minutes
        )
        sp.apply_proposal(
            db,
            graph,
            proposal,
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


@router.post("/study-plans/{plan_id}/items/{item_id}/complete", response_model=ItemDetail)
async def complete_item(
    plan_id: uuid.UUID, item_id: uuid.UUID, db: AsyncSession = Depends(get_session)
) -> ItemDetail:
    """Plan progress only. No card, score, session, or SM-2 field is read here."""
    graph = await _get_graph(db, plan_id)
    item = graph.item_by_id.get(item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="item not found")
    if item.status == ITEM_COMPLETE:
        raise HTTPException(status_code=409, detail="item is already complete")

    item.status = ITEM_COMPLETE
    item.completed_at = _now()
    item.updated_at = item.completed_at
    db.add(item)
    await db.commit()
    return await _item_detail(db, graph, item)


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
    item = graph.item_by_id.get(item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="item not found")
    if item.status != ITEM_COMPLETE:
        raise HTTPException(status_code=409, detail="item is not complete")

    plan, weeks, items, deps = graph.sched_inputs()
    items = [
        sched.SchedItem(**{**i.__dict__, "status": ITEM_PENDING})
        if i.id == str(item_id)
        else i
        for i in items
    ]
    proposal = sched.build_proposal(plan, weeks, items, deps)
    headline, text = _replan_copy(proposal)
    return _proposal_out(
        graph,
        proposal,
        kind="reopen",
        headline=headline if proposal.moves or proposal.unresolved
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
    graph = await _get_graph(db, plan_id)
    _require_current(graph.plan, base_plan_revision)
    item = graph.item_by_id.get(item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="item not found")
    if item.status != ITEM_COMPLETE:
        raise HTTPException(status_code=409, detail="item is not complete")

    before = sp.plan_snapshot(graph)
    item.status = ITEM_PENDING
    item.reopened_at = _now()
    item.completed_at = None
    item.updated_at = item.reopened_at
    db.add(item)

    proposal = sp.build_proposal_for(graph)
    if not proposal.valid:
        raise HTTPException(
            status_code=409, detail="reopening needs a replan; preview and apply it first"
        )

    graph.plan.revision += 1
    graph.plan.updated_at = _now()
    db.add(graph.plan)
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
    graph = await _get_graph(db, plan_id)
    item = graph.item_by_id.get(item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="item not found")

    before = sp.plan_snapshot(graph)
    changed_estimate = (
        body.estimate_minutes is not None and body.estimate_minutes != item.estimate_minutes
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
        # a revision. The proposal is not applied here — if it no longer fits, the
        # week reads as over capacity and the app offers the replan.
        proposal = sp.build_proposal_for(graph)
        graph.plan.revision += 1
        db.add(graph.plan)
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
async def pause_plan(
    plan_id: uuid.UUID, db: AsyncSession = Depends(get_session)
) -> PlanOverview:
    graph = await _get_graph(db, plan_id)
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
    graph = await _get_graph(db, plan_id)
    _require_current(graph.plan, base_plan_revision)
    if graph.plan.status == PLAN_ACTIVE:
        raise HTTPException(status_code=409, detail="plan is already active")

    proposal = sp.build_proposal_for(graph)
    if not proposal.valid:
        raise HTTPException(
            status_code=409, detail="resuming needs a replan; preview and apply it first"
        )

    await sp.make_active(db, graph, kind=REVISION_RESUME, summary="Plan resumed")
    await _commit_activation(db)
    return await _overview(db, plan_id)


@router.post("/study-plans/{plan_id}/activate", response_model=PlanOverview)
async def activate_plan(
    plan_id: uuid.UUID,
    base_plan_revision: int = Query(...),
    db: AsyncSession = Depends(get_session),
) -> PlanOverview:
    graph = await _get_graph(db, plan_id)
    _require_current(graph.plan, base_plan_revision)
    await sp.make_active(
        db, graph, kind=REVISION_ACTIVATE, summary="Made this the active plan"
    )
    await _commit_activation(db)
    return await _overview(db, plan_id)


@router.post("/study-plans/{plan_id}/complete", response_model=PlanOverview)
async def complete_plan(
    plan_id: uuid.UUID, db: AsyncSession = Depends(get_session)
) -> PlanOverview:
    graph = await _get_graph(db, plan_id)
    sp.complete(db, graph)
    await db.commit()
    return await _overview(db, plan_id)


@router.post("/study-plans/{plan_id}/archive", response_model=PlanOverview)
async def archive_plan(
    plan_id: uuid.UUID, db: AsyncSession = Depends(get_session)
) -> PlanOverview:
    graph = await _get_graph(db, plan_id)
    sp.archive(db, graph)
    await db.commit()
    return await _overview(db, plan_id)


@router.post("/study-plans/{plan_id}/duplicate", response_model=PlanOverview, status_code=201)
async def duplicate(
    plan_id: uuid.UUID, db: AsyncSession = Depends(get_session)
) -> PlanOverview:
    """Copy the plan's structure and reset its progress. Creates it paused."""
    graph = await _get_graph(db, plan_id)
    rows = sp.duplicate_plan(
        graph.plan, graph, start_date=spi.default_start_date(await local_today(db))
    )
    await _insert_plan_rows(db, rows["plan"], rows)
    db.add(
        StudyPlanDuplication(source_plan_id=graph.plan.id, duplicated_plan_id=rows["plan"].id)
    )
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
    item = graph.item_by_id.get(item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="item not found")
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
                "This subject uses plan-local retrieval activities. Devmax recall "
                "cards are only proposed for subjects the scoring rubric can grade."
            ),
        )

    existing = (
        await db.exec(
            select(StudyPlanCardProposal).where(
                col(StudyPlanCardProposal.source_plan_item_id) == item_id
            )
        )
    ).all()
    if not existing:
        weak = (
            await db.exec(
                select(Card)
                .where(col(Card.last_score).is_not(None))
                .where(col(Card.last_score) <= 3)
                .order_by(col(Card.last_score))
                .limit(10)
            )
        ).all()
        candidates = await llm.propose_cards(
            subject=graph.plan.subject,
            item_title=item.full_title,
            why_it_matters=item.why_it_matters,
            done_when=item.done_when,
            source_excerpt=item.source_excerpt,
            existing_weak_topics=[c.topic for c in weak],
        )
        existing_cards = await sp.normalized_card_index(db)
        for candidate in candidates[: sp.MAX_SUGGESTED_CARDS]:
            topic = str(candidate.get("topic", "")).strip()
            if not topic:
                continue
            gate = sp.normalise_gate_results(candidate.get("gate", []))
            exact = existing_cards.get(sp.normalize_topic(topic))
            duplicate_result, card_id = sp.classify_duplicate(exact, None)
            row = StudyPlanCardProposal(
                plan_id=plan_id,
                source_plan_item_id=item_id,
                topic=topic,
                category=str(candidate.get("category", "Unsorted")).strip() or "Unsorted",
                canonical_question=str(candidate.get("canonical_question", "")).strip(),
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

    rows: list[CardProposalOut] = []
    for proposal in existing:
        context = ""
        if proposal.duplicate_card_id:
            card = (
                await db.exec(select(Card).where(col(Card.id) == proposal.duplicate_card_id))
            ).first()
            if card is not None:
                score = card.last_score if card.last_score is not None else "—"
                context = f"Existing · score {score} · due {card.next_review_at.isoformat()}"
        rows.append(_proposal_response(proposal, context))

    suggested = sum(1 for r in rows if r.disposition == DISPOSITION_SUGGESTED)
    return CardProposalList(
        plan_id=plan_id,
        item_id=item_id,
        supports_recall_cards=True,
        suggested_count=suggested,
        proposals=rows,
        note=CARD_BOUNDARY_NOTE,
    )


@router.get(
    "/study-plans/{plan_id}/items/{item_id}/card-proposals",
    response_model=CardProposalList,
)
async def list_card_proposals(
    plan_id: uuid.UUID, item_id: uuid.UUID, db: AsyncSession = Depends(get_session)
) -> CardProposalList:
    """Read stored proposals. Never calls the model."""
    plan = await _get_plan(db, plan_id)
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
    acceptance record commit together, or nothing does — a half-created batch
    that reported success would leave the user unable to tell which cards exist.

    The exact normalized-topic check runs **again here**, inside the transaction,
    because a card can be written by hand between Preview and Accept. If one
    appeared, the batch aborts and that candidate refreshes as EXISTING rather
    than the rest being created while claiming atomic success.
    """
    plan = await _get_plan(db, plan_id)
    today = await local_today(db)

    prior = (
        await db.exec(
            select(StudyPlanCardProposalAcceptance).where(
                col(StudyPlanCardProposalAcceptance.idempotency_key) == body.idempotency_key
            )
        )
    ).first()
    computed_hash = sp.request_hash(body.selected_proposal_ids, body.edits)

    if prior is not None:
        if prior.request_hash != computed_hash:
            # Same key, different intent. This is a client bug, not a replay, and
            # silently honouring either request would create the wrong cards.
            raise HTTPException(
                status_code=409, detail="idempotency key reused with a different request"
            )
        if prior.status == ACCEPTANCE_COMMITTED:
            return CardAcceptOut(
                status="committed",
                created_card_ids=[uuid.UUID(i) for i in prior.created_card_ids],
                replayed=True,
            )
        # A previous attempt rolled back. Nothing was created, so it is safe to
        # run again under the same key.
        await db.delete(prior)
        await db.flush()

    proposals = []
    for proposal_id in body.selected_proposal_ids:
        row = (
            await db.exec(
                select(StudyPlanCardProposal).where(col(StudyPlanCardProposal.id) == proposal_id)
            )
        ).first()
        if row is None:
            raise HTTPException(status_code=404, detail="proposal not found")
        if row.plan_id != plan_id:
            raise HTTPException(status_code=404, detail="proposal not found")
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
    resolved: list[tuple[StudyPlanCardProposal, str, str]] = []
    existing_cards = await sp.normalized_card_index(db)
    for row in proposals:
        edit = body.edits.get(str(row.id), {})
        topic = (edit.get("topic") or row.topic).strip()
        question = (edit.get("canonical_question") or row.canonical_question).strip()

        clash = existing_cards.get(sp.normalize_topic(topic))
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
        resolved.append((row, topic, question))

    acceptance = StudyPlanCardProposalAcceptance(
        proposal_id=proposals[0].id,
        idempotency_key=body.idempotency_key,
        request_hash=computed_hash,
        proposal_revision=body.proposal_revision,
        status=ACCEPTANCE_PROCESSING,
    )
    db.add(acceptance)

    created: list[uuid.UUID] = []
    links: list[StudyPlanCardLink] = []
    for row, topic, question in resolved:
        card = Card(
            topic=topic,
            category=row.category,
            delivery_mode=DELIVERY_CONVERSATIONAL,
            # The approved question becomes the card's canonical question, so the
            # first review reuses it rather than generating a different one.
            canonical_question=question or None,
            next_review_at=today,
        )
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
    # would otherwise emit them first — see `_insert_plan_rows` for why ordering
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
        raise HTTPException(
            status_code=409, detail="nothing was created; try again"
        ) from exc

    # Card ids are returned only after commit, so ADDED can only render on cards
    # that exist.
    return CardAcceptOut(status="committed", created_card_ids=created, replayed=False)


@router.post("/study-plans/{plan_id}/card-proposals/resolve-duplicate", status_code=204)
async def resolve_duplicate(
    plan_id: uuid.UUID, body: DuplicateResolution, db: AsyncSession = Depends(get_session)
) -> Response:
    """Record the user's call on a possible overlap. Never decides it for them."""
    await _get_plan(db, plan_id)
    row = (
        await db.exec(
            select(StudyPlanCardProposal).where(col(StudyPlanCardProposal.id) == body.proposal_id)
        )
    ).first()
    if row is None or row.plan_id != plan_id:
        raise HTTPException(status_code=404, detail="proposal not found")

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
