"""Durable public guide imports and reviewed topic proposals."""

import uuid
from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

from fastapi import HTTPException
from sqlalchemy import delete
from sqlmodel import col, select

from app.config import get_settings
from app.db import session_factory
from app.models import (
    DRAFT_FAILED,
    ITEM_PRACTICE,
    PROPOSAL_CLEAN,
    PROPOSAL_NEEDS_ATTENTION,
    SOURCE_FAILED,
    SOURCE_NEEDS_ATTENTION,
    SOURCE_PENDING,
    SOURCE_PROCESSING,
    SOURCE_READY,
    MaterialSource,
    MaterialTopicProposal,
    Settings,
    StudyPlanGuideDraft,
)
from app.services import guide_import, llm, study_plan_import, usage
from app.services import study_plan as study_plan_service


def _now() -> datetime:
    return datetime.now(UTC)


async def _start_date(db, user_id: uuid.UUID) -> date:
    row = (await db.exec(select(Settings).where(Settings.user_id == user_id))).first()
    try:
        today = datetime.now(ZoneInfo(row.timezone if row else "UTC")).date()
    except (ValueError, KeyError):
        today = date.today()
    return study_plan_import.default_start_date(today)


async def process_import(source_id: uuid.UUID) -> None:
    """Run or resume one DB-backed import outside a request lifecycle."""
    async with session_factory() as db:
        source = await db.get(MaterialSource, source_id)
        if source is None or source.status not in {
            SOURCE_PENDING,
            SOURCE_PROCESSING,
            SOURCE_FAILED,
        }:
            return
        source.status = SOURCE_PROCESSING
        source.error = ""
        source.updated_at = _now()
        db.add(source)
        await db.commit()

        try:
            await usage.ensure_available(db, source.user_id, "guide_import", get_settings())
            # Long guides can spend minutes at the provider. End the quota-read
            # transaction so the durable job does not pin a pooled connection.
            await db.commit()
            if source.import_path == "plan":
                await _process_plan(db, source)
            else:
                await _process_topics(db, source)
            usage.record(db, source.user_id, "guide_import")
        except HTTPException as exc:
            source.status = SOURCE_FAILED
            source.error = str(exc.detail)
        except (llm.LLMError, study_plan_import.ImportError_) as exc:
            source.status = SOURCE_FAILED
            source.error = str(exc)
        source.updated_at = _now()
        db.add(source)
        await db.commit()


async def _process_plan(db, source: MaterialSource) -> None:
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

    await guide_import.run_plan_draft(db, draft)
    db.add(draft)
    source.error = draft.error
    if draft.status == DRAFT_FAILED:
        source.status = SOURCE_FAILED
        source.result_summary = {
            "draft_id": str(draft.id),
            "checks": 0,
            "can_create": False,
        }
        return

    clean, attention, comparison = await _store_topic_proposals(db, source, draft.preview)
    checks_ready = bool(draft.checks) and all(check.get("status") == "ok" for check in draft.checks)
    source.status = SOURCE_NEEDS_ATTENTION if attention or not checks_ready else SOURCE_READY
    source.result_summary = {
        "draft_id": str(draft.id),
        "checks": len(draft.checks),
        "can_create": checks_ready,
        "clean_count": clean,
        "attention_count": attention,
        "subject": draft.preview.get("subject", "Study material"),
        "comparison": comparison,
    }


async def _process_topics(db, source: MaterialSource) -> None:
    raw = await llm.import_guide(
        guide_text=source.source_text,
        requested_weeks=source.requested_weeks,
        weekly_capacity_minutes=source.weekly_capacity_minutes,
        mode=source.mode,
        deadline=source.deadline.isoformat() if source.deadline else None,
        subject_hint="",
        title_hint=source.title,
    )
    validated = study_plan_import.validate_import(
        raw,
        guide_text=source.source_text,
        requested_weeks=source.requested_weeks,
        weekly_capacity_minutes=source.weekly_capacity_minutes,
        mode=source.mode,
        deadline=source.deadline,
        start_date=await _start_date(db, source.user_id),
        resolutions={},
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
