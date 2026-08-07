"""Shared durable guide-import orchestration.

The Study Plan endpoint and public background importer both use this path, so a
retry cannot validate the same model response under two different rules.
"""

from datetime import UTC, datetime

from sqlmodel.ext.asyncio.session import AsyncSession

from app.models import DRAFT_FAILED, DRAFT_READY, StudyPlanGuideDraft
from app.services import llm, study_plan_import


def _now() -> datetime:
    return datetime.now(UTC)


async def run_plan_draft(db: AsyncSession, draft: StudyPlanGuideDraft) -> None:
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
    revalidate_plan_draft(draft)


def revalidate_plan_draft(draft: StudyPlanGuideDraft) -> None:
    resolutions = dict((draft.preview or {}).get("resolutions", {}))
    try:
        result = study_plan_import.validate_import(
            draft.raw_response,
            guide_text=draft.guide_text,
            requested_weeks=draft.requested_weeks,
            weekly_capacity_minutes=draft.weekly_capacity_minutes,
            mode=draft.mode,
            deadline=draft.deadline,
            start_date=draft.start_date,
            resolutions=resolutions,
        )
    except study_plan_import.ImportError_ as exc:
        draft.status = DRAFT_FAILED
        draft.error = str(exc)
        draft.updated_at = _now()
        return

    draft.preview = result.preview
    draft.checks = [check.as_dict() for check in result.checks]
    draft.status = DRAFT_READY
    draft.error = ""
    draft.updated_at = _now()
