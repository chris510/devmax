"""Durable public guide imports and reviewed topic proposals."""

import asyncio
import uuid
from contextlib import suppress
from copy import deepcopy
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
from app.services.card_lifecycle import Grounding, GroundingError, clean_rubric


def _now() -> datetime:
    return datetime.now(UTC)


_OPEN_QUESTION_PREFIXES = tuple(
    f"{starter} " for starter in llm.LESSON_OPEN_QUESTION_STARTERS
)
LESSON_GROUNDING_GATE_VERSION = 1
LESSON_GROUNDING_ISSUE_PREFIX = "Source grounding could not verify:"


def _is_open_question(value: str) -> bool:
    question = value.strip()
    return (
        question.endswith("?")
        and len(question.split()) >= 4
        and question.startswith(_OPEN_QUESTION_PREFIXES)
    )


def confidence_for(recall_score: int | None) -> str:
    """A deterministic export label from the latest unaided Recall score."""
    if recall_score is None:
        return "unrated"
    if recall_score <= 2:
        return "needs_review"
    if recall_score == 3:
        return "developing"
    return "established"


def render_lesson_markdown(
    source: MaterialSource, concepts: list[dict]
) -> tuple[str, str]:
    """Render distilled artifacts without copying raw source or transcripts."""
    source_line = source.title
    if source.source_url:
        source_line = f"[{source.title}](<{source.source_url}>)"

    note_lines = [f"# {source.title}", "", f"Source: {source_line}"]
    recall_lines = [f"# {source.title} — Recall questions"]
    for concept in concepts:
        note_lines.extend(
            [
                "",
                f"## {concept['concept']}",
                "",
                concept["mental_model"],
                "",
                "### How it works",
                "",
                concept["how_it_works"],
            ]
        )
        gotchas = concept.get("gotchas") or []
        if gotchas:
            note_lines.extend(["", "### Gotchas and trade-offs", ""])
            note_lines.extend(f"- {value}" for value in gotchas)

        prompts = concept["recall_prompts"]
        note_lines.extend(["", "### Recall prompts", ""])
        recall_lines.extend(["", f"## {concept['concept']}"])
        for prompt in prompts:
            label = prompt["level"].replace("_", " / ").title()
            line = f"- **{label}:** {prompt['question']}"
            note_lines.append(line)
            recall_lines.append(line)

        quiz_results = concept.get("quiz_results") or []
        if quiz_results:
            note_lines.extend(["", "### Quiz results", ""])
            for result in quiz_results:
                reviewed_at = result["reviewed_at"]
                reviewed_on = (
                    reviewed_at.date().isoformat()
                    if isinstance(reviewed_at, datetime)
                    else str(reviewed_at)
                )
                score = result.get("recall_score")
                score_copy = f"Recall {score}/5" if score is not None else "graded"
                summary = result.get("graded_summary") or result.get("feedback") or ""
                note_lines.append(
                    f"- {reviewed_on} · {score_copy} · {summary}".rstrip(" ·")
                )

    return "\n".join(note_lines).strip() + "\n", "\n".join(recall_lines).strip() + "\n"


class _ImportClaimLost(Exception):
    """The result has no live account/worker claim to return to."""


def _guide_authorizer(
    db: AsyncSession,
    user_id: uuid.UUID,
    source_id: uuid.UUID,
    run_id: uuid.UUID,
    *,
    model: str | None = None,
    operation: str = "guide_import",
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
        operation,
        config=config,
        provider="anthropic",
        model=model or config.studyplan_model,
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
                elif source.import_path == "lesson":
                    source = await guide_import.run_while_heartbeat_live(
                        _process_lesson(db, source, run_id), heartbeat
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


async def _process_lesson(
    db: AsyncSession, source: MaterialSource, run_id: uuid.UUID
) -> MaterialSource:
    concepts = await llm.extract_lesson(
        title=source.title,
        source_text=source.source_text,
        source_url=source.source_url,
        source_type=source.kind,
        before_provider_call=_guide_authorizer(
            db,
            source.user_id,
            source.id,
            run_id,
            model=get_settings().card_proposal_model,
        ),
    )
    concepts, grounding_issues = await _ground_lesson_concepts(
        db, source, run_id, concepts
    )
    source = await _lock_result_claim(
        db,
        user_id=source.user_id,
        source_id=source.id,
        run_id=run_id,
    )
    clean, attention, comparison = await _store_lesson_proposals(
        db,
        source,
        concepts,
        grounding_issues=grounding_issues,
    )
    source.status = SOURCE_NEEDS_ATTENTION if attention else SOURCE_READY
    source.result_summary = {
        "workflow": "lesson",
        "concept_count": clean + attention,
        "clean_count": clean,
        "attention_count": attention,
        "subject": source.title,
        "comparison": comparison,
        "grounding_gate_version": LESSON_GROUNDING_GATE_VERSION,
    }
    return source


def _validated_lesson_concepts(
    source: MaterialSource, concepts: list[dict]
) -> list[dict]:
    """Fail the extraction atomically unless every concept is source-grounded."""
    if not 1 <= len(concepts) <= 7:
        raise llm.LLMError("lesson extraction must return between 1 and 7 concepts")

    validated: list[dict] = []
    normalized_topics: set[str] = set()
    for index, concept in enumerate(concepts, 1):
        if not isinstance(concept, dict):
            raise llm.LLMError(f"lesson concept {index} is not an object")
        topic = str(concept.get("topic", "")).strip()
        section = str(concept.get("section_title", "")).strip()
        excerpt = str(concept.get("source_excerpt", "")).strip()
        answer_basis = str(concept.get("answer_basis", "")).strip()
        canonical_question = str(concept.get("canonical_question", "")).strip()
        raw_rubric = concept.get("answer_rubric")
        if not isinstance(raw_rubric, dict):
            raise llm.LLMError(
                f"lesson concept {index} answer rubric is not an object"
            )
        rubric = clean_rubric(raw_rubric)
        prompts = concept.get("recall_questions")

        normalized = study_plan_service.normalize_topic(topic)
        if not normalized or normalized in normalized_topics:
            raise llm.LLMError(
                f"lesson concept {index} has an empty or duplicate topic"
            )
        if not section:
            raise llm.LLMError(
                f"lesson concept {index} has an empty section title"
            )
        normalized_topics.add(normalized)
        if len(topic) > 200 or len(section) > 1000:
            raise llm.LLMError(
                f"lesson concept {index} topic or section is too long"
            )
        if len(excerpt) > 20_000 or len(answer_basis) > 2000:
            raise llm.LLMError(
                f"lesson concept {index} grounding is too long"
            )
        if len(canonical_question) > 2000:
            raise llm.LLMError(
                f"lesson concept {index} canonical question is too long"
            )
        if any(len(value) > 900 for value in rubric.values()):
            raise llm.LLMError(
                f"lesson concept {index} answer rubric is too long"
            )
        if not excerpt or excerpt not in source.source_text:
            raise llm.LLMError(
                f"lesson concept {index} excerpt is not verbatim source text"
            )
        if not _is_open_question(canonical_question):
            raise llm.LLMError(
                f"lesson concept {index} canonical question is not open-ended"
            )
        if not isinstance(prompts, list) or len(prompts) != len(
            llm.LESSON_RECALL_LEVELS
        ):
            raise llm.LLMError(
                f"lesson concept {index} must have exactly five recall prompts"
            )

        cleaned_prompts: list[dict[str, str]] = []
        for expected_level, prompt in zip(
            llm.LESSON_RECALL_LEVELS, prompts, strict=True
        ):
            if not isinstance(prompt, dict):
                raise llm.LLMError(
                    f"lesson concept {index} has an invalid recall prompt"
                )
            level = str(prompt.get("level", "")).strip()
            question = str(prompt.get("question", "")).strip()
            if (
                level != expected_level
                or len(question) > 1000
                or not _is_open_question(question)
            ):
                raise llm.LLMError(
                    f"lesson concept {index} has invalid {expected_level} recall"
                )
            cleaned_prompts.append({"level": level, "question": question})

        try:
            grounding = Grounding(
                source_url=source.source_url,
                source_section=section,
                source_label=source.title,
                answer_basis=answer_basis,
                answer_rubric=rubric,
                canonical_question=canonical_question,
            ).require_complete()
        except GroundingError as exc:
            raise llm.LLMError(
                f"lesson concept {index} has incomplete grounding: {', '.join(exc.missing)}"
            ) from exc

        validated.append(
            {
                "topic": topic,
                "section_title": section,
                "source_excerpt": excerpt,
                "answer_basis": grounding.answer_basis,
                "canonical_question": grounding.canonical_question,
                "answer_rubric": rubric,
                "recall_questions": cleaned_prompts,
            }
        )
    return validated


def _validated_lesson_grounding(
    source: MaterialSource,
    concepts: list[dict],
    findings: list[dict],
) -> dict[tuple[int, str], dict]:
    """Require one literal-evidence verdict for every user-visible field."""
    expected = {
        (concept_index, field)
        for concept_index in range(1, len(concepts) + 1)
        for field in llm.LESSON_GROUNDING_FIELDS
    }
    validated: dict[tuple[int, str], dict] = {}
    for finding_index, finding in enumerate(findings, 1):
        if not isinstance(finding, dict):
            raise llm.LLMError(
                f"lesson grounding finding {finding_index} is not an object"
            )
        concept_index = finding.get("concept_index")
        field = finding.get("field")
        if (
            isinstance(concept_index, bool)
            or not isinstance(concept_index, int)
            or not isinstance(field, str)
        ):
            raise llm.LLMError(
                f"lesson grounding finding {finding_index} has an invalid field key"
            )
        key = (concept_index, field)
        if key not in expected or key in validated:
            raise llm.LLMError(
                f"lesson grounding finding {finding_index} is unexpected or duplicated"
            )

        verdict = finding.get("verdict")
        reason = finding.get("reason")
        repair = finding.get("repair")
        spans = finding.get("evidence_spans")
        if verdict not in llm.LESSON_GROUNDING_VERDICTS:
            raise llm.LLMError(
                f"lesson grounding finding {finding_index} has an invalid verdict"
            )
        if (
            verdict == "bounded_absence"
            and field not in llm.LESSON_BOUNDED_ABSENCE_FIELDS
        ):
            raise llm.LLMError(
                f"lesson grounding finding {finding_index} uses bounded absence "
                "for a required positive field"
            )
        if (
            not isinstance(reason, str)
            or not reason.strip()
            or len(reason) > 800
            or not isinstance(repair, str)
            or len(repair) > 2000
            or not isinstance(spans, list)
            or len(spans) > 4
            or sum(len(span) for span in spans if isinstance(span, str)) > 1200
        ):
            raise llm.LLMError(
                f"lesson grounding finding {finding_index} has invalid evidence"
            )
        if verdict != "unsupported" and repair.strip():
            raise llm.LLMError(
                f"lesson grounding finding {finding_index} repairs a passing field"
            )
        if verdict != "unsupported" and not spans:
            raise llm.LLMError(
                f"lesson grounding finding {finding_index} has no supporting span"
            )

        excerpt = concepts[concept_index - 1]["source_excerpt"]
        for span in spans:
            if (
                not isinstance(span, str)
                or not span.strip()
                or len(span) > 500
                or span not in source.source_text
                or span not in excerpt
            ):
                raise llm.LLMError(
                    f"lesson grounding finding {finding_index} has a non-literal span"
                )
        validated[key] = {
            "concept_index": concept_index,
            "field": field,
            "verdict": verdict,
            "evidence_spans": list(spans),
            "reason": reason.strip(),
            "repair": repair.strip(),
        }

    missing = expected - set(validated)
    if missing:
        raise llm.LLMError(
            f"lesson grounding is missing {len(missing)} required field verdicts"
        )
    return validated


def _unsupported_grounding(
    review: dict[tuple[int, str], dict],
) -> dict[int, list[dict]]:
    unsupported: dict[int, list[dict]] = {}
    for finding in review.values():
        if finding["verdict"] == "unsupported":
            unsupported.setdefault(finding["concept_index"], []).append(finding)
    return unsupported


def _grounding_issues(
    review: dict[tuple[int, str], dict],
) -> dict[int, str]:
    return {
        concept_index: _grounding_issue(findings)
        for concept_index, findings in _unsupported_grounding(review).items()
    }


def _grounding_issue(findings: list[dict]) -> str:
    fields = sorted({finding["field"] for finding in findings})
    return f"{LESSON_GROUNDING_ISSUE_PREFIX} {', '.join(fields)}."


def _set_lesson_field(concept: dict, field: str, value: str) -> None:
    if field in {"topic", "section_title", "answer_basis", "canonical_question"}:
        concept[field] = value
        return
    rubric_prefix = "answer_rubric."
    if field.startswith(rubric_prefix):
        concept["answer_rubric"][field.removeprefix(rubric_prefix)] = value
        return
    recall_prefix = "recall_questions."
    if field.startswith(recall_prefix):
        level = field.removeprefix(recall_prefix)
        prompt = next(
            item
            for item in concept["recall_questions"]
            if item.get("level") == level
        )
        prompt["question"] = value
        return
    raise llm.LLMError(f"lesson grounding repair has an unknown field: {field}")


async def _ground_lesson_concepts(
    db: AsyncSession,
    source: MaterialSource,
    run_id: uuid.UUID,
    concepts: list[dict],
) -> tuple[list[dict], dict[int, str]]:
    """Verify once, then allow at most one repair-and-reverify pass."""
    original = _validated_lesson_concepts(source, concepts)
    first_findings = await llm.verify_lesson_grounding(
        source_text=source.source_text,
        concepts=original,
        before_provider_call=_guide_authorizer(
            db,
            source.user_id,
            source.id,
            run_id,
            model=get_settings().card_proposal_model,
            operation="lesson_grounding",
        ),
    )
    first_review = _validated_lesson_grounding(
        source, original, first_findings
    )
    first_unsupported = _unsupported_grounding(first_review)
    repairable = {
        concept_index
        for concept_index, findings in first_unsupported.items()
        if all(finding["repair"] for finding in findings)
    }
    if not repairable:
        return original, _grounding_issues(first_review)

    repaired = deepcopy(original)
    for concept_index in repairable:
        for finding in first_unsupported[concept_index]:
            _set_lesson_field(
                repaired[concept_index - 1],
                finding["field"],
                finding["repair"],
            )
    try:
        repaired = _validated_lesson_concepts(source, repaired)
    except llm.LLMError:
        # A structurally invalid repair is advisory only. Keep the original
        # concepts review-only rather than failing or trusting a partial change.
        return original, _grounding_issues(first_review)

    second_findings = await llm.verify_lesson_grounding(
        source_text=source.source_text,
        concepts=repaired,
        before_provider_call=_guide_authorizer(
            db,
            source.user_id,
            source.id,
            run_id,
            model=get_settings().card_proposal_model,
            operation="lesson_grounding_recheck",
        ),
    )
    second_review = _validated_lesson_grounding(source, repaired, second_findings)
    second_unsupported = _unsupported_grounding(second_review)
    issues = {
        concept_index: _grounding_issue(findings)
        for concept_index, findings in second_unsupported.items()
    }
    first_issues = _grounding_issues(first_review)
    for concept_index in first_unsupported.keys() - repairable:
        issues[concept_index] = first_issues[concept_index]
    for concept_index in repairable & second_unsupported.keys():
        # Restoring the original pack also restores every first-pass rejected
        # field, so the review issue must name both rejection sets.
        issues[concept_index] = _grounding_issue(
            [
                *first_unsupported[concept_index],
                *second_unsupported[concept_index],
            ]
        )
    accepted = deepcopy(repaired)
    for concept_index in issues:
        # A rejected second-pass repair is advisory only. Keep the exact
        # originally extracted pack reviewable; never persist text that the
        # independent verifier just rejected.
        accepted[concept_index - 1] = deepcopy(original[concept_index - 1])
    return accepted, issues


async def _store_lesson_proposals(
    db: AsyncSession,
    source: MaterialSource,
    concepts: list[dict],
    *,
    grounding_issues: dict[int, str] | None = None,
) -> tuple[int, int, dict[str, int]]:
    """Validate the complete concept pack, then replace its durable preview."""
    validated = _validated_lesson_concepts(source, concepts)
    existing = await study_plan_service.normalized_card_index(db, source.user_id)
    rows: list[MaterialTopicProposal] = []
    current: dict[str, str] = {}
    clean = 0
    attention = 0
    for position, concept in enumerate(validated, 1):
        normalized = study_plan_service.normalize_topic(concept["topic"])
        issues = []
        if grounding_issues and grounding_issues.get(position):
            issues.append(grounding_issues[position])
        if normalized in existing:
            issues.append("A topic with this name already exists in your library.")
        issue = " ".join(issues)
        status = PROPOSAL_NEEDS_ATTENTION if issue else PROPOSAL_CLEAN
        attention += status == PROPOSAL_NEEDS_ATTENTION
        clean += status == PROPOSAL_CLEAN
        current[normalized] = concept["answer_basis"]
        rows.append(
            MaterialTopicProposal(
                source_id=source.id,
                position=position,
                section_title=concept["section_title"],
                topic=concept["topic"],
                answer_anchor=concept["answer_basis"],
                source_excerpt=concept["source_excerpt"],
                canonical_question=concept["canonical_question"],
                answer_rubric=concept["answer_rubric"],
                recall_questions=concept["recall_questions"],
                status=status,
                issue=issue,
            )
        )

    comparison = await _proposal_comparison(db, source, current)
    await db.exec(
        delete(MaterialTopicProposal).where(
            MaterialTopicProposal.source_id == source.id
        )
    )
    for row in rows:
        db.add(row)
    return clean, attention, comparison


async def confirm_source_version(db, source: MaterialSource, user_id: uuid.UUID) -> None:
    """Confirm one source and supersede its owned predecessor, if any."""
    source.status = SOURCE_CONFIRMED
    db.add(source)
    if source.previous_version_id:
        previous = await db.get(MaterialSource, source.previous_version_id)
        if previous is not None and previous.user_id == user_id:
            previous.status = SOURCE_SUPERSEDED
            db.add(previous)


async def _proposal_comparison(
    db: AsyncSession,
    source: MaterialSource,
    current: dict[str, str],
) -> dict[str, int]:
    if not source.previous_version_id:
        return {}
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
    return {
        "added": len(current.keys() - previous.keys()),
        "changed": sum(previous[key] != current[key] for key in shared),
        "removed": len(previous.keys() - current.keys()),
        "unchanged": sum(previous[key] == current[key] for key in shared),
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
    return clean, attention, await _proposal_comparison(db, source, current)


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
