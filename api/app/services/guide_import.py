"""Shared durable guide-import orchestration.

The Study Plan endpoint and public background importer both use this path, so a
retry cannot validate the same model response under two different rules.
"""

import asyncio
from collections.abc import Awaitable, Mapping
from contextlib import suppress
from datetime import UTC, date, datetime, timedelta
from typing import Any

from app.models import DRAFT_FAILED, DRAFT_READY, StudyPlanGuideDraft
from app.services import llm, study_plan_import

GUIDE_IMPORT_HEARTBEAT_SECONDS = 15
GUIDE_IMPORT_STALE_AFTER = timedelta(minutes=2)

def _now() -> datetime:
    return datetime.now(UTC)


async def run_while_heartbeat_live[ResultT](
    work: Awaitable[ResultT], heartbeat: asyncio.Task[None]
) -> ResultT:
    """Cancel provider work if the durable lease monitor exits first."""
    provider_work = asyncio.create_task(work)
    done, _ = await asyncio.wait(
        {provider_work, heartbeat}, return_when=asyncio.FIRST_COMPLETED
    )
    if heartbeat in done:
        provider_work.cancel()
        with suppress(asyncio.CancelledError):
            await provider_work
        # Heartbeat tasks exit only by exception. Awaiting preserves the exact
        # lost-claim versus unavailable-database failure for terminal handling.
        await heartbeat
        raise RuntimeError("import heartbeat exited without an error")  # pragma: no cover
    return await provider_work


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
    before_provider_call: llm.BeforeProviderCall,
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
        before_provider_call=before_provider_call,
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


def copy_plan_draft_result(
    target: StudyPlanGuideDraft, source: StudyPlanGuideDraft
) -> None:
    """Copy only the provider/import result fields onto a locked draft row."""
    target.status = source.status
    target.preview = source.preview
    target.raw_response = source.raw_response
    target.checks = source.checks
    target.error = source.error
    target.updated_at = source.updated_at


async def run_plan_draft(
    draft: StudyPlanGuideDraft, *, before_provider_call: llm.BeforeProviderCall
) -> None:
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
            before_provider_call=before_provider_call,
        )
    except llm.LLMError:
        draft.status = DRAFT_FAILED
        # Provider/parser exceptions can embed raw model output. The original
        # guide is already retained on the draft; do not create a second,
        # operationally exposed copy inside an error string.
        draft.error = "AI import unavailable. Retry to continue."
        draft.updated_at = _now()
        return
    except study_plan_import.ImportError_ as exc:
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
