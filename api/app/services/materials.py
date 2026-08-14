"""Durable public guide imports and reviewed topic proposals."""

import asyncio
import uuid
from contextlib import suppress
from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

from fastapi import HTTPException
from sqlalchemy import and_, delete, or_, update
from sqlalchemy.exc import SQLAlchemyError
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.config import get_settings
from app.db import session_factory
from app.models import (
    DRAFT_FAILED,
    ITEM_PRACTICE,
    PROPOSAL_CLEAN,
    PROPOSAL_NEEDS_ATTENTION,
    SOURCE_CONFIRMED,
    SOURCE_FAILED,
    SOURCE_NEEDS_ATTENTION,
    SOURCE_PENDING,
    SOURCE_PROCESSING,
    SOURCE_READY,
    SOURCE_SUPERSEDED,
    MaterialSource,
    MaterialTopicProposal,
    Settings,
    StudyPlanGuideDraft,
)
from app.services import guide_import, llm, study_plan_import, usage
from app.services import study_plan as study_plan_service


def _now() -> datetime:
    return datetime.now(UTC)


class _ImportClaimLost(Exception):
    """The result has no live account/worker claim to return to."""


def _guide_authorizer(
    db: AsyncSession,
    user_id: uuid.UUID,
    source_id: uuid.UUID,
    run_id: uuid.UUID,
):
    config = get_settings()

    async def require_live_claim(boundary_db: AsyncSession) -> None:
        source = (
            await boundary_db.exec(
                select(MaterialSource.id)
                .where(
                    MaterialSource.id == source_id,
                    MaterialSource.user_id == user_id,
                    MaterialSource.status == SOURCE_PROCESSING,
                    MaterialSource.processing_run_id == run_id,
                )
                .with_for_update()
            )
        ).first()
        if source is None:
            raise HTTPException(status_code=409, detail="material import claim lost")

    return usage.provider_call_authorizer(
        db,
        user_id,
        "guide_import",
        config=config,
        provider="anthropic",
        model=config.studyplan_model,
        boundary_check=require_live_claim,
    )


async def _claim_import(
    db: AsyncSession, source_id: uuid.UUID
) -> tuple[MaterialSource, uuid.UUID] | None:
    """Atomically claim pending work or an orphan whose heartbeat expired."""
    now = _now()
    run_id = uuid.uuid4()
    stale_before = now - guide_import.GUIDE_IMPORT_STALE_AFTER
    statement = (
        update(MaterialSource)
        .where(MaterialSource.id == source_id)
        .where(
            or_(
                MaterialSource.status == SOURCE_PENDING,
                and_(
                    MaterialSource.status == SOURCE_PROCESSING,
                    or_(
                        MaterialSource.processing_heartbeat_at.is_(None),
                        MaterialSource.processing_heartbeat_at <= stale_before,
                    ),
                ),
            )
        )
        .values(
            status=SOURCE_PROCESSING,
            processing_run_id=run_id,
            processing_heartbeat_at=now,
            error="",
            updated_at=now,
        )
        .returning(MaterialSource.id)
    )
    claimed_id = (await db.exec(statement)).one_or_none()
    if claimed_id is None:
        await db.rollback()
        return None
    await db.commit()
    source = await db.get(MaterialSource, claimed_id, populate_existing=True)
    if source is None:  # Account/source deletion won immediately after claim.
        await db.rollback()
        return None
    return source, run_id


async def _heartbeat_import(source_id: uuid.UUID, run_id: uuid.UUID) -> None:
    """Renew a live claim without holding the worker's pooled connection."""
    consecutive_failures = 0
    while True:
        await asyncio.sleep(guide_import.GUIDE_IMPORT_HEARTBEAT_SECONDS)
        try:
            async with session_factory() as heartbeat_db:
                result = await heartbeat_db.exec(
                    update(MaterialSource)
                    .where(
                        MaterialSource.id == source_id,
                        MaterialSource.status == SOURCE_PROCESSING,
                        MaterialSource.processing_run_id == run_id,
                    )
                    .values(processing_heartbeat_at=_now())
                )
                await heartbeat_db.commit()
                if result.rowcount != 1:
                    raise _ImportClaimLost
            consecutive_failures = 0
        except _ImportClaimLost:
            raise
        except SQLAlchemyError as exc:
            consecutive_failures += 1
            if consecutive_failures >= 3:
                raise HTTPException(
                    status_code=503,
                    detail="material import lease could not be renewed",
                ) from exc


async def _lock_result_claim(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    source_id: uuid.UUID,
    run_id: uuid.UUID,
) -> MaterialSource:
    """Lock account then source and prove this worker still owns the result."""
    if not await usage.lock_account_for_provider_result(db, user_id):
        raise _ImportClaimLost
    source = (
        await db.exec(
            select(MaterialSource)
            .where(
                MaterialSource.id == source_id,
                MaterialSource.user_id == user_id,
                MaterialSource.status == SOURCE_PROCESSING,
                MaterialSource.processing_run_id == run_id,
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    ).first()
    if source is None:
        raise _ImportClaimLost
    return source


async def _start_date(db, user_id: uuid.UUID) -> date:
    row = (await db.exec(select(Settings).where(Settings.user_id == user_id))).first()
    try:
        today = datetime.now(ZoneInfo(row.timezone if row else "UTC")).date()
    except (ValueError, KeyError):
        today = date.today()
    return study_plan_import.default_start_date(today)


async def process_import(source_id: uuid.UUID) -> bool:
    """Run one atomically claimed import; return whether this worker claimed it."""
    async with session_factory() as db:
        claim = await _claim_import(db, source_id)
        if claim is None:
            return False
        source, run_id = claim
        heartbeat = asyncio.create_task(
            _heartbeat_import(source.id, run_id),
            name=f"material-heartbeat-{source.id}",
        )

        try:
            try:
                if source.import_path == "plan":
                    source = await guide_import.run_while_heartbeat_live(
                        _process_plan(db, source, run_id), heartbeat
                    )
                else:
                    source = await guide_import.run_while_heartbeat_live(
                        _process_topics(db, source, run_id), heartbeat
                    )
            except (HTTPException, llm.LLMError, study_plan_import.ImportError_) as exc:
                source = await _lock_result_claim(
                    db,
                    user_id=source.user_id,
                    source_id=source.id,
                    run_id=run_id,
                )
                source.status = SOURCE_FAILED
                source.error = (
                    str(exc.detail) if isinstance(exc, HTTPException) else str(exc)
                )
            source.processing_run_id = None
            source.processing_heartbeat_at = None
            source.updated_at = _now()
            db.add(source)
            await db.commit()
            return True
        except _ImportClaimLost:
            # Deletion or a stale-claim takeover won.  The old provider result
            # must not recreate children or overwrite the current claimant.
            await db.rollback()
            return True
        finally:
            if not heartbeat.done():
                heartbeat.cancel()
            with suppress(asyncio.CancelledError, _ImportClaimLost, HTTPException):
                await heartbeat


async def delete_source(
    db: AsyncSession, *, source_id: uuid.UUID, user_id: uuid.UUID
) -> bool:
    """Delete uploaded text and its source-owned preview under one boundary.

    A created StudyPlan keeps its separately committed guide provenance, but the
    transient preview/raw response used only by this MaterialSource must not
    retain another verbatim copy after the user deletes the upload.
    """
    if not await usage.lock_account_for_provider_result(db, user_id):
        await db.rollback()
        return False
    source = (
        await db.exec(
            select(MaterialSource)
            .where(
                MaterialSource.id == source_id,
                MaterialSource.user_id == user_id,
            )
            .with_for_update()
        )
    ).first()
    if source is None:
        await db.rollback()
        return False
    draft_id = source.plan_draft_id
    await db.delete(source)
    await db.flush()
    if draft_id is not None:
        another_owner = (
            await db.exec(
                select(MaterialSource.id).where(
                    MaterialSource.plan_draft_id == draft_id
                )
            )
        ).first()
        if another_owner is None:
            draft = await db.get(StudyPlanGuideDraft, draft_id, with_for_update=True)
            if draft is not None:
                await db.delete(draft)
    await db.commit()
    return True


async def _process_plan(
    db: AsyncSession, source: MaterialSource, run_id: uuid.UUID
) -> MaterialSource:
    draft = (
        await db.get(StudyPlanGuideDraft, source.plan_draft_id) if source.plan_draft_id else None
    )
    if draft is None:
        draft = StudyPlanGuideDraft(
            user_id=source.user_id,
            guide_text=source.source_text,
            requested_weeks=source.requested_weeks,
            weekly_capacity_minutes=source.weekly_capacity_minutes,
            mode=source.mode,
            deadline=source.deadline,
            start_date=await _start_date(db, source.user_id),
            title_hint=source.title,
        )
        db.add(draft)
        await db.flush()
        source.plan_draft_id = draft.id
        db.add(source)
        await db.commit()

    # Keep provider mutations detached. Deleting the source also deletes this
    # transient draft; a late response must reach the source/account claim check
    # before SQLAlchemy can autoflush an UPDATE against that deleted row.
    working = StudyPlanGuideDraft.model_validate(draft.model_dump())
    await guide_import.run_plan_draft(
        working,
        before_provider_call=_guide_authorizer(
            db, source.user_id, source.id, run_id
        ),
    )
    source = await _lock_result_claim(
        db,
        user_id=source.user_id,
        source_id=source.id,
        run_id=run_id,
    )
    draft = await db.get(
        StudyPlanGuideDraft,
        source.plan_draft_id,
        with_for_update=True,
        populate_existing=True,
    )
    if draft is None:
        raise _ImportClaimLost
    guide_import.copy_plan_draft_result(draft, working)
    db.add(draft)
    source.error = working.error
    if working.status == DRAFT_FAILED:
        source.status = SOURCE_FAILED
        source.result_summary = {
            "draft_id": str(draft.id),
            "checks": 0,
            "can_create": False,
        }
        return source

    clean, attention, comparison = await _store_topic_proposals(
        db, source, working.preview
    )
    checks_ready = bool(working.checks) and all(
        check.get("status") == "ok" for check in working.checks
    )
    source.status = SOURCE_NEEDS_ATTENTION if attention or not checks_ready else SOURCE_READY
    source.result_summary = {
        "draft_id": str(draft.id),
        "checks": len(working.checks),
        "can_create": checks_ready,
        "clean_count": clean,
        "attention_count": attention,
        "subject": working.preview.get("subject", "Study material"),
        "comparison": comparison,
    }
    return source


async def _process_topics(
    db: AsyncSession, source: MaterialSource, run_id: uuid.UUID
) -> MaterialSource:
    _, validated = await guide_import.import_and_validate(
        guide_text=source.source_text,
        requested_weeks=source.requested_weeks,
        weekly_capacity_minutes=source.weekly_capacity_minutes,
        mode=source.mode,
        deadline=source.deadline,
        start_date=await _start_date(db, source.user_id),
        subject_hint="",
        title_hint=source.title,
        before_provider_call=_guide_authorizer(
            db, source.user_id, source.id, run_id
        ),
    )
    source = await _lock_result_claim(
        db,
        user_id=source.user_id,
        source_id=source.id,
        run_id=run_id,
    )
    preview = validated.preview
    clean, attention, comparison = await _store_topic_proposals(db, source, preview)
    source.status = SOURCE_NEEDS_ATTENTION if attention else SOURCE_READY
    source.result_summary = {
        "clean_count": clean,
        "attention_count": attention,
        "subject": preview.get("subject", "Study material"),
        "comparison": comparison,
    }
    return source


async def confirm_source_version(db, source: MaterialSource, user_id: uuid.UUID) -> None:
    """Confirm one source and supersede its owned predecessor, if any."""
    source.status = SOURCE_CONFIRMED
    db.add(source)
    if source.previous_version_id:
        previous = await db.get(MaterialSource, source.previous_version_id)
        if previous is not None and previous.user_id == user_id:
            previous.status = SOURCE_SUPERSEDED
            db.add(previous)


async def _store_topic_proposals(
    db, source: MaterialSource, preview: dict
) -> tuple[int, int, dict[str, int]]:
    """Store review proposals from an already-validated guide preview."""
    await db.exec(delete(MaterialTopicProposal).where(MaterialTopicProposal.source_id == source.id))
    existing = await study_plan_service.normalized_card_index(db, source.user_id)
    attention = 0
    clean = 0
    current: dict[str, str] = {}
    for position, item in enumerate(preview.get("items", []), 1):
        topic = str(item.get("full_title", "")).strip()
        excerpt = str(item.get("source_excerpt", "")).strip()
        anchor = str(item.get("done_when") or item.get("why_it_matters") or excerpt).strip()
        issue = ""
        if item.get("type") == ITEM_PRACTICE:
            issue = "This activity is plan-only and is not supported for conversational review."
        elif not anchor:
            issue = "A source-grounded answer anchor is required."
        elif study_plan_service.normalize_topic(topic) in existing:
            issue = "A topic with this name already exists in your library."
        status = PROPOSAL_NEEDS_ATTENTION if issue else PROPOSAL_CLEAN
        attention += status == PROPOSAL_NEEDS_ATTENTION
        clean += status == PROPOSAL_CLEAN
        normalized_topic = study_plan_service.normalize_topic(topic)
        if normalized_topic:
            current[normalized_topic] = anchor
        db.add(
            MaterialTopicProposal(
                source_id=source.id,
                position=position,
                section_title=str(item.get("week_title") or item.get("phase_title") or ""),
                topic=topic or f"Source topic {position}",
                answer_anchor=anchor,
                source_excerpt=excerpt,
                status=status,
                issue=issue,
            )
        )
    comparison: dict[str, int] = {}
    if source.previous_version_id:
        previous_rows = (
            await db.exec(
                select(MaterialTopicProposal).where(
                    MaterialTopicProposal.source_id == source.previous_version_id
                )
            )
        ).all()
        previous = {
            study_plan_service.normalize_topic(row.topic): row.answer_anchor
            for row in previous_rows
        }
        shared = previous.keys() & current.keys()
        comparison = {
            "added": len(current.keys() - previous.keys()),
            "changed": sum(previous[key] != current[key] for key in shared),
            "removed": len(previous.keys() - current.keys()),
            "unchanged": sum(previous[key] == current[key] for key in shared),
        }
    return clean, attention, comparison


async def resume_imports() -> list[uuid.UUID]:
    async with session_factory() as db:
        ids = (
            await db.exec(
                select(MaterialSource.id).where(
                    col(MaterialSource.status).in_((SOURCE_PENDING, SOURCE_PROCESSING))
                )
            )
        ).all()
    return list(ids)


async def resume_import(source_id: uuid.UUID) -> None:
    """Recover startup work once any live worker's heartbeat actually expires."""
    while True:
        if await process_import(source_id):
            return
        async with session_factory() as db:
            source = await db.get(MaterialSource, source_id)
            if source is None or source.status not in {
                SOURCE_PENDING,
                SOURCE_PROCESSING,
            }:
                return
            if source.status == SOURCE_PENDING or source.processing_heartbeat_at is None:
                delay = 1.0
            else:
                heartbeat = source.processing_heartbeat_at
                if heartbeat.tzinfo is None:
                    heartbeat = heartbeat.replace(tzinfo=UTC)
                delay = max(
                    1.0,
                    (
                        heartbeat + guide_import.GUIDE_IMPORT_STALE_AFTER - _now()
                    ).total_seconds(),
                )
        await asyncio.sleep(delay)
