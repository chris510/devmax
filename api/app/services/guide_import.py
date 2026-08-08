"""Shared durable guide-import orchestration.

The Study Plan endpoint and public background importer both use this path, so a
retry cannot validate the same model response under two different rules.
"""

from collections.abc import Mapping
from datetime import UTC, date, datetime
from typing import Any

from app.models import DRAFT_FAILED, DRAFT_READY, StudyPlanGuideDraft
from app.services import llm, study_plan_import


def _now() -> datetime:
    return datetime.now(UTC)


async def import_and_validate(
    *,
    guide_text: str,
    requested_weeks: int,
    weekly_capacity_minutes: int,
    mode: str,
    deadline: date | None,
    start_date: date,
    subject_hint: str = "",
    title_hint: str = "",
    resolutions: Mapping[str, Any] | None = None,
) -> tuple[dict[str, Any], study_plan_import.ImportResult]:
    """Ask the model once, then apply the shared deterministic import gate."""
    raw = await llm.import_guide(
        guide_text=guide_text,
        requested_weeks=requested_weeks,
        weekly_capacity_minutes=weekly_capacity_minutes,
        mode=mode,
        deadline=deadline.isoformat() if deadline else None,
        subject_hint=subject_hint,
        title_hint=title_hint,
    )
    result = study_plan_import.validate_import(
        raw,
        guide_text=guide_text,
        requested_weeks=requested_weeks,
        weekly_capacity_minutes=weekly_capacity_minutes,
        mode=mode,
        deadline=deadline,
        start_date=start_date,
        resolutions=resolutions or {},
    )
    return raw, result


def _apply_result(draft: StudyPlanGuideDraft, result: study_plan_import.ImportResult) -> None:
    draft.preview = result.preview
    draft.checks = [check.as_dict() for check in result.checks]
    draft.status = DRAFT_READY
    draft.error = ""
    draft.updated_at = _now()


async def run_plan_draft(draft: StudyPlanGuideDraft) -> None:
    try:
        raw, result = await import_and_validate(
            guide_text=draft.guide_text,
            requested_weeks=draft.requested_weeks,
            weekly_capacity_minutes=draft.weekly_capacity_minutes,
            mode=draft.mode,
            deadline=draft.deadline,
            start_date=draft.start_date,
            subject_hint=draft.subject_hint,
            title_hint=draft.title_hint,
            resolutions=dict((draft.preview or {}).get("resolutions", {})),
        )
    except (llm.LLMError, study_plan_import.ImportError_) as exc:
        draft.status = DRAFT_FAILED
        draft.error = str(exc)
        draft.updated_at = _now()
        return

    draft.raw_response = raw
    _apply_result(draft, result)


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

    _apply_result(draft, result)
