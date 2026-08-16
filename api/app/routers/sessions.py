import uuid
from collections.abc import Sequence
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy import exists, func, update
from sqlalchemy.exc import IntegrityError
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.auth import current_user_id
from app.config import get_settings
from app.db import get_session
from app.models import (
    CARD_ACTIVE,
    LIVE_STATUSES,
    STATUS_ABANDONED,
    STATUS_AWAITING_FOLLOW_UP,
    STATUS_COMPLETE,
    STATUS_OPEN,
    Card,
    Session,
    SessionProbe,
)
from app.routers.deps import as_utc, local_today, owned_card
from app.schemas import (
    AnswerIn,
    CoachingOut,
    CompleteOut,
    DraftUpdate,
    FollowUpOut,
    ReattemptOut,
    ScoredAnswerIn,
    SessionStart,
)
from app.services import ai_consent, llm, usage
from app.services.cards import recall_is_available
from app.services.scheduler import apply_sm2, quality_for
from app.services.scoring_contract import (
    SCORING_CONTRACT_V2,
    active_scoring_contract_version,
    coaching_question,
    next_coaching_focus,
)
from app.services.scoring_provider import (
    ROUTE_ANTHROPIC,
    ROUTE_PRIMARY,
    ROUTE_SHADOW,
    ScoringRoute,
    ScoringTrace,
    openai_route_eligibility,
    qualification_fingerprint,
    route_for_session,
)

router = APIRouter(tags=["sessions"])

RECENT_QUESTION_LIMIT = 3

# The accuracy band where the rubric states the correct answer outright, and so the
# only band where there is anything to re-attempt. Same threshold the scheduler fails
# on (`scheduler.ACCURACY_PASS`), but they are independent decisions — one is "was
# this a retention failure", the other is "is coaching available". Do not collapse.
REATTEMPT_MAX_ACCURACY = 2

# Turn 3 re-asks the card's own question rather than generating a new one: the
# re-attempt is the same retrieval, now informed. Composed server-side and sent to
# the client, so what is displayed is what the answer is graded against.
REATTEMPT_PREFACE = "In your words — "


def _scoring_event_details(
    *,
    session: Session,
    probes_used: int,
    event_id: uuid.UUID,
    event_started_at: datetime,
    consent_verified: bool,
    allowlist_verified: bool,
    qualification_expires_at: str,
) -> dict[str, object]:
    """Build metadata shared by a scoring intent and its terminal call rows."""
    return {
        "scoring_event_id": str(event_id),
        "event_started_at": event_started_at.isoformat(),
        "session_id": str(session.id),
        "scoring_contract_version": session.scoring_contract_version,
        "probes_used": probes_used,
        "ai_consent_policy_version": ai_consent.POLICY_VERSION,
        "ai_consent_verified": consent_verified,
        "openai_allowlist_verified": allowlist_verified,
        "qualification_expires_at": qualification_expires_at,
    }


def _scoring_call_details(
    trace: ScoringTrace | None,
    *,
    session: Session,
    probes_used: int,
    event_id: uuid.UUID,
    event_started_at: datetime,
    consent_verified: bool,
    allowlist_verified: bool,
    qualification_expires_at: str,
) -> list[dict[str, object]] | None:
    """Add correlation metadata without copying learner content into telemetry."""
    if trace is None:
        return None
    event_details = _scoring_event_details(
        session=session,
        probes_used=probes_used,
        event_id=event_id,
        event_started_at=event_started_at,
        consent_verified=consent_verified,
        allowlist_verified=allowlist_verified,
        qualification_expires_at=qualification_expires_at,
    )
    return [
        {
            **details,
            **event_details,
        }
        for details in trace.usage_details()
    ]


def _scoring_intent_details(
    *,
    session: Session,
    probes_used: int,
    event_id: uuid.UUID,
    event_started_at: datetime,
    consent_verified: bool,
    allowlist_verified: bool,
    reserved_calls: int,
    openai_expected: bool,
    shadow_stage_id: str,
    qualification_expires_at: str,
) -> dict[str, object]:
    """Describe expected calls without copying any scoring input or output."""
    route = session.scoring_route
    mode = str(route.get("mode", ROUTE_ANTHROPIC))
    expected_calls: list[dict[str, str]]
    if not openai_expected or mode == ROUTE_ANTHROPIC:
        expected_calls = [
            {
                "provider": "anthropic",
                "model": str(route.get("anthropic_model", "")),
                "requirement": "required",
            }
        ]
        authoritative_provider = "anthropic"
    elif mode == ROUTE_SHADOW:
        expected_calls = [
            {
                "provider": "anthropic",
                "model": str(route.get("anthropic_model", "")),
                "requirement": "required",
            },
            {
                "provider": "openai",
                "model": str(route.get("openai_model", "")),
                "requirement": "required",
            },
        ]
        authoritative_provider = "anthropic"
    elif mode == ROUTE_PRIMARY:
        expected_calls = [
            {
                "provider": "openai",
                "model": str(route.get("openai_model", "")),
                "requirement": "required",
            },
            {
                "provider": "anthropic",
                "model": str(route.get("anthropic_model", "")),
                "requirement": "conditional_fallback",
            },
        ]
        authoritative_provider = "openai"
    else:  # pragma: no cover - callers emit intents only for OpenAI routes
        raise ValueError("scoring intent requires an OpenAI route")
    return {
        "audit_type": "scoring_event_intent",
        "manifest_version": 1,
        "status": "pending",
        "reserved_calls": reserved_calls,
        "finalized_at": None,
        "terminal_call_count": 0,
        "shadow_stage_id": shadow_stage_id,
        "shadow_stage_ordinal": None,
        "route": mode,
        "authoritative_provider": authoritative_provider,
        "qualification_fingerprint": str(
            route.get("qualification_fingerprint", "")
        ),
        **_scoring_event_details(
            session=session,
            probes_used=probes_used,
            event_id=event_id,
            event_started_at=event_started_at,
            consent_verified=consent_verified,
            allowlist_verified=allowlist_verified,
            qualification_expires_at=qualification_expires_at,
        ),
        "expected_calls": expected_calls,
    }


def _reattempt_eligible(session: Session) -> bool:
    """Whether a completed session may still be coached.

    One predicate, two callers: `submit_answer` decides whether to offer the link
    and `submit_reattempt` decides whether to honour it. Written once so an offer
    the endpoint would 409 is unrepresentable — DEVIATIONS §15 shows this band does
    move, and it must move in both places at once.
    """
    return session.accuracy is not None and session.accuracy <= REATTEMPT_MAX_ACCURACY


async def _session_probes(db: AsyncSession, session_id: uuid.UUID) -> list[SessionProbe]:
    """This session's scored probes, oldest first. `idx` is the whole ordering."""
    return list(
        (
            await db.exec(
                select(SessionProbe)
                .where(SessionProbe.session_id == session_id)
                .order_by(col(SessionProbe.idx))
            )
        ).all()
    )


def _pending_probe(session: Session, probes: Sequence[SessionProbe]) -> SessionProbe | None:
    """The probe this session is waiting on, or None when it is on its first turn.

    `awaiting_follow_up` means "a scored probe is pending"; *which* one is the
    last row, written unanswered the moment its question was issued. Reading it
    here is what keeps the status column free of a probe number.
    """
    if session.status != STATUS_AWAITING_FOLLOW_UP or not probes:
        return None
    last = probes[-1]
    return last if not last.answer else None


def _replayed_answer(
    session: Session,
    probes: Sequence[SessionProbe],
    submitted_text: str,
    submitted_turn_index: int | None,
) -> FollowUpOut | None:
    """Return the probe produced by an exact replay of any committed past turn.

    The client deliberately retries a failed submission with the same saved text.
    If the server committed a follow-up but its response was lost, the session is
    already awaiting the next turn when that retry arrives. Treating the duplicate
    request as the *next* answer would let one piece of recall evidence occupy two
    scored turns. `turn_index` distinguishes that retry from a genuine next answer
    even when the learner uses the same words twice.

    A turn-aware request is addressable across the whole stored transcript: turn
    0's answer is on the session, turn N's is probe N, and the response produced
    by either is probe N+1. This remains reconstructible after later probes or
    completion. Only the pre-index compatibility path is limited to the
    immediately preceding turn.
    """
    if submitted_turn_index is not None:
        response_probe = next(
            (probe for probe in probes if probe.idx == submitted_turn_index + 1),
            None,
        )
        if response_probe is None:
            return None
        if submitted_turn_index == 0:
            answered = session.answer_text
        else:
            answered_probe = next(
                (probe for probe in probes if probe.idx == submitted_turn_index),
                None,
            )
            if answered_probe is None:  # pragma: no cover - probe order cannot gap
                return None
            answered = answered_probe.answer
        if answered != submitted_text:
            raise HTTPException(
                status_code=409,
                detail="answer turn was already committed with different text",
            )
        return FollowUpOut(
            question=response_probe.question, turn_index=response_probe.idx
        )
    # Compatibility for clients that shipped before `turn_index`. It preserves
    # their lost-follow-up recovery but cannot distinguish the same words spoken
    # again on the next probe; turn-aware clients never enter this branch.
    pending = _pending_probe(session, probes)
    if pending is None or not pending.question:
        return None
    answered = probes[-2].answer if len(probes) >= 2 else session.answer_text
    if answered == submitted_text:
        return FollowUpOut(question=pending.question, turn_index=pending.idx)
    return None


def _is_completed_answer_replay(
    session: Session,
    probes: Sequence[SessionProbe],
    submitted_text: str,
    submitted_turn_index: int | None,
) -> bool:
    """Whether this is the exact scored turn that completed the session.

    A terminal response can disappear after its transaction commits. The client
    then retries the same disk-backed text. The final answer is already durable —
    either on the session or on its last probe. Session id plus `turn_index` names
    the turn; equality verifies that the key was not reused with different text.
    A pre-index client still gets 409 here rather than an ambiguous text-only replay.
    """
    if session.status != STATUS_COMPLETE or submitted_turn_index is None:
        return False
    answered = probes[-1].answer if probes else session.answer_text
    answered_turn_index = probes[-1].idx if probes else 0
    if submitted_turn_index != answered_turn_index:
        return False
    if answered != submitted_text:
        raise HTTPException(
            status_code=409,
            detail="answer turn was already committed with different text",
        )
    return True


def _card_was_reviewed_since(card: Card, session: Session) -> bool:
    """Whether a later review superseded this session's card-side result."""
    return (
        card.last_reviewed_at is not None
        and session.ended_at is not None
        and as_utc(card.last_reviewed_at) > as_utc(session.ended_at)
    )


def _card_still_has_session_result(card: Card, session: Session) -> bool:
    """Whether current schedule fields still reconstruct this completion."""
    return (
        card.last_reviewed_at is not None
        and session.ended_at is not None
        and as_utc(card.last_reviewed_at) == as_utc(session.ended_at)
    )


def _replayed_coaching(session: Session, submitted_text: str) -> CoachingOut | None:
    """Rebuild a committed qualitative turn from its immutable stored fields."""
    if (
        session.coaching_answer != submitted_text
        or session.coaching_focus is None
        or session.coaching_question is None
        or session.coaching_feedback is None
    ):
        return None
    return CoachingOut(
        focus=session.coaching_focus,
        question=session.coaching_question,
        feedback=session.coaching_feedback,
    )


async def _resumed(db: AsyncSession, existing: Session) -> SessionStart:
    """Re-enter a live session at the turn it is actually waiting on.

    On a probe turn the displayed question is the pending probe's, not the card's
    — the same shared read both resume paths in `start_session` use, so a second
    probe cannot show the first one's text on one path and not the other.
    """
    is_follow_up = existing.status == STATUS_AWAITING_FOLLOW_UP
    question = existing.question_asked
    turn_index = 0
    if is_follow_up:
        pending = _pending_probe(existing, await _session_probes(db, existing.id))
        question = pending.question if pending else ""
        turn_index = pending.idx if pending else 0
    return SessionStart(
        session_id=existing.id,
        question=question,
        is_follow_up=is_follow_up,
        turn_index=turn_index,
        draft_text=existing.draft_text,
        resumed=True,
        practice=existing.practice,
    )


async def _resume_or_conflict(
    db: AsyncSession, existing: Session, requested_practice: bool
) -> SessionStart:
    """Resume only when the caller asked for the session's frozen mode.

    Practice and scheduled reviews write the same mastery signal but have
    opposite scheduling semantics. Silently returning a scheduled session to a
    practice caller (or vice versa) makes the later answer do something the
    caller did not request.
    """
    if existing.practice != requested_practice:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "session_mode_conflict",
                "session_id": str(existing.id),
                "practice": existing.practice,
            },
        )
    return await _resumed(db, existing)


async def _live_session(db: AsyncSession, card_id: uuid.UUID) -> Session | None:
    return (
        await db.exec(
            select(Session)
            .where(Session.card_id == card_id, col(Session.status).in_(LIVE_STATUSES))
            .order_by(col(Session.started_at).desc())
        )
    ).first()


async def _recent_questions(db: AsyncSession, card_id: uuid.UUID) -> list[str]:
    return list(
        (
            await db.exec(
                select(Session.question_asked)
                .where(Session.card_id == card_id)
                .order_by(col(Session.started_at).desc())
                .limit(RECENT_QUESTION_LIMIT)
            )
        ).all()
    )


def _question_generation_snapshot(
    card: Card, recent_questions: list[str]
) -> tuple[object, ...]:
    """Every durable input that can influence the one canonical question."""
    return (
        card.topic,
        card.category,
        card.pattern,
        card.source_company,
        card.mastery_summary,
        card.last_score,
        tuple(recent_questions),
        card.answer_anchor,
        card.source_excerpt,
        card.answer_basis,
        tuple(sorted(card.answer_rubric.items())),
    )


async def _owned_session(
    db: AsyncSession, session_id: uuid.UUID, *, for_update: bool = False
) -> Session | None:
    owned = exists().where(
        Card.id == Session.card_id,
        Card.user_id == current_user_id(),
    )
    statement = select(Session).where(Session.id == session_id, owned)
    if for_update:
        # Draft/answer synchronization owns only the session row. In particular,
        # PATCH /draft never waits on the card lock held across provider work.
        statement = statement.with_for_update(of=Session).execution_options(
            populate_existing=True
        )
    return (await db.exec(statement)).first()


def _complete_response(session: Session, card: Card) -> CompleteOut:
    """Build the terminal wire result from the fields committed atomically.

    This is shared by the first response and an exact retry, so the replay cannot
    drift from the ordinary completion contract. Schedule fields are read from the
    card only while this session remains its latest review.
    """
    eligible = _reattempt_eligible(session)
    coaching_offered = (
        session.scoring_contract_version == SCORING_CONTRACT_V2
        and session.accuracy is not None
        and session.accuracy >= 3
    )
    # V2 freezes these beside the score. A historical/incomplete row with no
    # stored offer fails closed rather than deriving one from later card history.
    focus = session.coaching_focus if coaching_offered else None
    question = session.coaching_question if coaching_offered else None
    coaching_offered = coaching_offered and focus is not None and question is not None
    return CompleteOut(
        score=session.score or 0,
        recall_score=session.accuracy or 0,
        scoring_contract_version=session.scoring_contract_version,
        feedback=session.feedback,
        next_review_at=card.next_review_at,
        interval_days=card.interval_days,
        practice=session.practice,
        reattempt_offered=eligible,
        reattempt_prompt=REATTEMPT_PREFACE + session.question_asked if eligible else None,
        coaching_offered=coaching_offered,
        coaching_focus=focus if coaching_offered else None,
        coaching_question=question if coaching_offered else None,
    )


def _require_recall_available(card: Card) -> None:
    if not recall_is_available(card, datetime.now(UTC)):
        value = card.recall_not_before_at
        if value is not None and value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        raise HTTPException(
            status_code=409,
            detail={
                "code": "recall_cooldown",
                "recall_available_at": value.isoformat() if value else None,
            },
        )


@router.post("/cards/{card_id}/sessions", response_model=SessionStart)
async def start_session(
    card_id: uuid.UUID,
    practice: bool = Query(False),
    db: AsyncSession = Depends(get_session),
) -> SessionStart:
    """Called when the user taps into a card — not when the push fires.

    Question generation happens here, on actual engagement: generating one for a
    push that may never be opened wastes tokens and latency. It also happens at
    most once per card — after the first session the question is reused verbatim,
    so each review is the same retrieval rather than a fresh one.
    """
    stale_generations = 0
    while True:
        card = await owned_card(db, card_id)
        if card is None:
            raise HTTPException(status_code=404, detail="card not found")
        if card.lifecycle_status != CARD_ACTIVE:
            raise HTTPException(status_code=409, detail="card is archived")
        _require_recall_available(card)

        existing = await _live_session(db, card_id)
        if existing is not None:
            # Returning the live session instead of creating a new one is what
            # makes resume work.
            return await _resume_or_conflict(db, existing, practice)

        question = card.canonical_question
        generated_question: str | None = None
        generation_snapshot: tuple[object, ...] | None = None
        if not question:
            await usage.ensure_available(
                db, current_user_id(), "question", get_settings()
            )
            # Concurrent first-opens serialize at the account/provider boundary
            # above. Refresh after that possible wait: the winner may already
            # have committed both the canonical question and its live session.
            await db.refresh(card)
            if card.lifecycle_status != CARD_ACTIVE:
                raise HTTPException(status_code=409, detail="card is archived")
            _require_recall_available(card)
            existing = await _live_session(db, card_id)
            if existing is not None:
                resumed = await _resume_or_conflict(db, existing, practice)
                await db.rollback()
                return resumed

            question = card.canonical_question
            if not question:
                recent = await _recent_questions(db, card_id)
                generation_snapshot = _question_generation_snapshot(card, recent)
                generated_question = await llm.generate_question(
                    topic=card.topic,
                    category=card.category,
                    pattern=card.pattern,
                    source_company=card.source_company,
                    mastery_summary=card.mastery_summary,
                    last_score=card.last_score,
                    recent_questions=recent,
                    answer_anchor=card.answer_anchor,
                    source_excerpt=card.source_excerpt,
                    answer_basis=card.answer_basis,
                    answer_rubric=card.answer_rubric,
                )
                usage.record(db, current_user_id(), "question")

        # Serialize the final eligibility check with Learn and maintenance.
        # Provider work stays outside the card lock; if any generation input
        # changed meanwhile, its result is accounted for but discarded.
        card = await owned_card(db, card_id, for_update=True)
        if card is None:  # pragma: no cover - the initial owned read found it
            raise HTTPException(status_code=404, detail="card not found")
        if card.lifecycle_status != CARD_ACTIVE:
            raise HTTPException(status_code=409, detail="card is archived")
        _require_recall_available(card)

        # A concurrent starter may have committed while this request generated a
        # question. Resume its session rather than creating a second one.
        existing = await _live_session(db, card_id)
        if existing is not None:
            return await _resume_or_conflict(db, existing, practice)

        if (
            not card.canonical_question
            and generated_question is not None
            and generation_snapshot is not None
        ):
            current_recent = await _recent_questions(db, card_id)
            if generation_snapshot != _question_generation_snapshot(
                card, current_recent
            ):
                # This commit records the physical call and releases the account
                # and card locks before a bounded retry against fresh authority.
                await db.commit()
                stale_generations += 1
                if stale_generations >= 2:
                    raise HTTPException(
                        status_code=409,
                        detail={"code": "question_inputs_changed"},
                    )
                continue

        question = card.canonical_question or generated_question
        if not question:  # pragma: no cover - generation rejects an empty question
            raise llm.LLMError("question generation returned an empty question")
        if not card.canonical_question:
            # Persisted before it is returned, so the same question comes back
            # next time. Clearing this column by hand is the way to re-roll one.
            card.canonical_question = question
            db.add(card)
        break

    scoring_contract_version = active_scoring_contract_version()
    frozen_scoring_route = route_for_session(
        get_settings(),
        user_id=current_user_id(),
        scoring_contract_version=scoring_contract_version,
    )
    session = Session(
        card_id=card_id,
        question_asked=question,
        practice=practice,
        status=STATUS_OPEN,
        scoring_contract_version=scoring_contract_version,
        scoring_route=frozen_scoring_route.as_json(),
    )
    db.add(session)
    try:
        await db.commit()
    except IntegrityError:
        # SQLite has no row-level FOR UPDATE. The partial unique index still
        # chooses one live session; turn a lost insert race into the same
        # resume/mode-conflict contract instead of leaking a 500.
        await db.rollback()
        winner = await _live_session(db, card_id)
        if winner is None:
            raise
        return await _resume_or_conflict(db, winner, practice)
    await db.refresh(session)

    return SessionStart(
        session_id=session.id,
        question=question,
        is_follow_up=False,
        turn_index=0,
        draft_text="",
        resumed=False,
        practice=session.practice,
    )


@router.patch("/sessions/{session_id}/draft", status_code=204)
async def save_draft(
    session_id: uuid.UUID, body: DraftUpdate, db: AsyncSession = Depends(get_session)
) -> Response:
    """Cheap, idempotent, never blocked behind anything slow.

    Losing a spoken answer is the worst failure mode in the product. The short
    session-row barrier orders this write after any answer commit, then a fresh
    statement snapshot decides whether the indexed turn is still current. It
    never takes the card lock held across provider work.
    """
    session = await _owned_session(db, session_id, for_update=True)
    if session is None:
        raise HTTPException(status_code=404, detail="session not found")

    statement = update(Session).where(Session.id == session.id)
    if body.turn_index is None:
        # Compatibility for pre-index clients. Refuse completed sessions at
        # least; only an index can distinguish a late turn-N upload from the
        # current turn while the session remains live.
        statement = statement.where(col(Session.status).in_(LIVE_STATUSES))
    elif body.turn_index == 0:
        statement = statement.where(Session.status == STATUS_OPEN)
    else:
        statement = statement.where(
            Session.status == STATUS_AWAITING_FOLLOW_UP,
            exists().where(
                SessionProbe.session_id == Session.id,
                SessionProbe.idx == body.turn_index,
                SessionProbe.answer == "",
            ),
        )
    await db.exec(
        statement.values(draft_text=body.draft_text).execution_options(
            synchronize_session=False
        )
    )
    await db.commit()
    return Response(status_code=204)


@router.post("/sessions/{session_id}/abandon", status_code=204)
async def abandon_session(
    session_id: uuid.UUID, db: AsyncSession = Depends(get_session)
) -> Response:
    """Explicitly end a live answer without scoring or moving its card.

    The draft remains durable as evidence for recovery/support. Abandonment is
    idempotent, but a completed session cannot be retroactively relabeled. The
    card lock serializes this transition with answer submission and maintenance.
    """
    session = await _owned_session(db, session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="session not found")
    if session.status == STATUS_ABANDONED:
        return Response(status_code=204)

    card = await owned_card(db, session.card_id, for_update=True)
    if card is None:  # pragma: no cover — FK guarantees this
        raise HTTPException(status_code=404, detail="card not found")
    await db.refresh(session)
    if session.status == STATUS_ABANDONED:
        return Response(status_code=204)
    if session.status == STATUS_COMPLETE:
        raise HTTPException(status_code=409, detail="completed sessions cannot be abandoned")
    if session.status not in LIVE_STATUSES:  # pragma: no cover — constrained in storage
        raise HTTPException(status_code=409, detail="session is not live")

    session.status = STATUS_ABANDONED
    session.ended_at = datetime.now(UTC)
    db.add(session)
    await db.commit()
    return Response(status_code=204)


@router.post("/sessions/{session_id}/answers", response_model=FollowUpOut | CompleteOut)
async def submit_answer(
    session_id: uuid.UUID, body: ScoredAnswerIn, db: AsyncSession = Depends(get_session)
) -> FollowUpOut | CompleteOut:
    # Empty string is the durable "unanswered probe" sentinel used by both
    # pending-turn lookup and indexed draft CAS. Never let user input commit the
    # same representation, and do not spend a provider call on no evidence.
    if not body.text.strip():
        raise HTTPException(status_code=422, detail="answer text is empty")
    session = await _owned_session(db, session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="session not found")
    # A completed idempotent replay makes no provider call and therefore must
    # remain available after consent is withdrawn. Re-check under the card lock
    # below for the concurrent case where another submit has not committed yet.
    prelock_probes = await _session_probes(db, session.id)
    prelock_replay = _replayed_answer(
        session, prelock_probes, body.text, body.turn_index
    )
    if prelock_replay is not None:
        return prelock_replay
    if _is_completed_answer_replay(
        session, prelock_probes, body.text, body.turn_index
    ):
        # Reconstruct only while this is still the card's latest review. A later
        # review changes the schedule fields carried by `CompleteOut`, so replaying
        # the old turn after that point could not return the committed response.
        card = await owned_card(db, session.card_id, for_update=True)
        if card is None:  # pragma: no cover — FK guarantees this
            raise HTTPException(status_code=404, detail="card not found")
        await db.refresh(session)
        probes = await _session_probes(db, session.id)
        if _is_completed_answer_replay(
            session, probes, body.text, body.turn_index
        ):
            if not _card_still_has_session_result(card, session):
                raise HTTPException(
                    status_code=409, detail="session result has been superseded"
                )
            return _complete_response(session, card)
    if session.status == STATUS_COMPLETE:
        raise HTTPException(status_code=409, detail="session already complete")
    if session.status not in LIVE_STATUSES:
        raise HTTPException(status_code=409, detail="session is abandoned")
    user_id = current_user_id()
    settings = get_settings()
    # Provider work and account deletion share an account -> card lock order.
    # Reversing it here can deadlock Postgres when DELETE /account cascades to
    # the same card while this request waits on the user's consent row.
    await ai_consent.require_ai_processing(db, user_id, settings)
    observed_status = session.status

    card = await owned_card(db, session.card_id, for_update=True)
    if card is None:  # pragma: no cover — FK guarantees this
        raise HTTPException(status_code=404, detail="card not found")
    # The card lock serializes reviews without blocking PATCH /draft, which only
    # touches the session row. Refresh after any wait. An exact replay of the last
    # answered turn may observe the committed probe either before the lock or after
    # waiting on it; in both cases return that same probe without scoring again.
    await db.refresh(session)
    probes = await _session_probes(db, session.id)
    if _is_completed_answer_replay(session, probes, body.text, body.turn_index):
        if not _card_still_has_session_result(card, session):
            raise HTTPException(
                status_code=409, detail="session result has been superseded"
            )
        return _complete_response(session, card)
    if card.lifecycle_status != CARD_ACTIVE:
        # Maintenance shares this lock, so a valid live session cannot normally
        # reach here on an archived card. Keep the answer write fail-closed if a
        # historical race or hand-edited row violates that structural invariant.
        raise HTTPException(status_code=409, detail="card is archived")
    replay = _replayed_answer(session, probes, body.text, body.turn_index)
    if replay is not None:
        return replay
    if session.status != observed_status:
        raise HTTPException(status_code=409, detail="session advanced during submission")
    if session.status not in LIVE_STATUSES:
        detail = (
            "session already complete"
            if session.status == STATUS_COMPLETE
            else "session is abandoned"
        )
        raise HTTPException(status_code=409, detail=detail)

    pending = _pending_probe(session, probes)
    if session.status == STATUS_AWAITING_FOLLOW_UP and pending is None:  # pragma: no cover
        # The status is only ever written beside an unanswered probe row, in the
        # same transaction. Refuse rather than score this answer as the initial one.
        raise HTTPException(status_code=409, detail="session has no pending probe")
    expected_turn_index = pending.idx if pending is not None else 0
    if body.turn_index is not None and body.turn_index != expected_turn_index:
        raise HTTPException(status_code=409, detail="answer turn does not match session")

    # The scored transcript so far, plus the answer being submitted now. Built
    # without touching the ORM objects: nothing is written until the score lands.
    # `len(pairs)` is the scorer's `probes_used`, which is what both the parser
    # policy and the cap below are keyed on.
    if pending is not None:
        answer_text = session.answer_text
        pairs = [(p.question, p.answer) for p in probes[:-1]]
        pairs.append((pending.question, body.text))
    else:
        answer_text = body.text
        pairs = []

    # Score before writing anything, so an LLM failure leaves the session and card
    # untouched rather than half-written. The answer is re-sent on retry.
    score_operation = (
        "score_v2" if session.scoring_contract_version == SCORING_CONTRACT_V2 else "score"
    )
    scoring_event_id = uuid.uuid4()
    scoring_event_started_at = datetime.now(UTC)
    route_mode = str(session.scoring_route.get("mode", ROUTE_ANTHROPIC))
    # Shadow always makes two calls; primary may need its one permitted technical
    # fallback. Check the worst case against the existing best-effort daily guard.
    # The provider account spend cap remains the hard concurrent-spend boundary.
    allowlist_verified_now = user_id in settings.openai_v2_scoring_user_id_set
    qualification_expires_at = (
        settings.openai_v2_scoring_qualification_expires_at.strip()
    )
    openai_eligible_now = False
    if session.scoring_contract_version == SCORING_CONTRACT_V2:
        try:
            frozen_route = ScoringRoute.from_json(session.scoring_route, settings)
        except ValueError as exc:
            await db.rollback()
            raise llm.LLMError(str(exc)) from exc
        if frozen_route.mode in {ROUTE_SHADOW, ROUTE_PRIMARY}:
            openai_completion = llm.build_score_v2_completion(
                model=frozen_route.openai_model,
                effort=frozen_route.openai_effort,
                topic=card.topic,
                mastery_summary=card.mastery_summary,
                question_asked=session.question_asked,
                answer_text=answer_text,
                probes=pairs,
                answer_anchor=card.answer_anchor,
                source_excerpt=card.source_excerpt,
                answer_basis=card.answer_basis,
                answer_rubric=card.answer_rubric,
            )
            eligibility = openai_route_eligibility(
                settings,
                route=frozen_route,
                user_id=user_id,
                actual_fingerprint=qualification_fingerprint(openai_completion),
            )
            openai_eligible_now = eligibility.allowed
    requested_calls = (
        1 if route_mode == ROUTE_ANTHROPIC or not openai_eligible_now else 2
    )
    await usage.ensure_available(
        db,
        user_id,
        score_operation,
        settings,
        requested_calls=requested_calls,
        consent_boundary_locked=True,
    )
    scoring_intent_id: uuid.UUID | None = None
    if session.scoring_contract_version == SCORING_CONTRACT_V2:
        # This independent, content-free manifest is the last durable action
        # before provider orchestration. A process death can leave a manifest
        # without terminal rows, but can no longer make a paid first-N canary
        # event disappear and be replaced by a later success.
        try:
            scoring_intent_id = await usage.record_scoring_intent(
                db,
                user_id,
                details=_scoring_intent_details(
                    session=session,
                    probes_used=len(pairs),
                    event_id=scoring_event_id,
                    event_started_at=scoring_event_started_at,
                    consent_verified=settings.ai_consent_enforcement_enabled,
                    allowlist_verified=allowlist_verified_now,
                    reserved_calls=requested_calls,
                    openai_expected=(
                        route_mode in {ROUTE_SHADOW, ROUTE_PRIMARY}
                        and openai_eligible_now
                    ),
                    shadow_stage_id=(
                        settings.openai_v2_scoring_shadow_stage_id
                        if route_mode == ROUTE_SHADOW and openai_eligible_now
                        else ""
                    ),
                    qualification_expires_at=qualification_expires_at,
                ),
            )
        except Exception as exc:
            await db.rollback()
            raise llm.LLMError("scoring audit unavailable") from exc
    try:
        result = await llm.score_answer(
            topic=card.topic,
            mastery_summary=card.mastery_summary,
            question_asked=session.question_asked,
            answer_text=answer_text,
            probes=pairs,
            answer_anchor=card.answer_anchor,
            source_excerpt=card.source_excerpt,
            answer_basis=card.answer_basis,
            answer_rubric=card.answer_rubric,
            scoring_contract_version=session.scoring_contract_version,
            scoring_route=session.scoring_route,
            user_id=user_id,
        )
    except llm.LLMError as exc:
        details = _scoring_call_details(
            exc.trace,
            session=session,
            probes_used=len(pairs),
            event_id=scoring_event_id,
            event_started_at=scoring_event_started_at,
            consent_verified=settings.ai_consent_enforcement_enabled,
            allowlist_verified=allowlist_verified_now,
            qualification_expires_at=qualification_expires_at,
        )
        if details:
            # No session/card mutation has happened yet. Persist only physical
            # call evidence so paid failures still count and remain diagnosable.
            try:
                independently_committed = await usage.record_physical_calls(
                    db,
                    user_id,
                    score_operation,
                    call_details=details,
                    intent_id=scoring_intent_id,
                )
            except Exception as audit_exc:
                await db.rollback()
                raise llm.LLMError("scoring audit unavailable") from audit_exc
            if not independently_committed:
                await db.commit()
        raise
    call_details = _scoring_call_details(
        result.trace,
        session=session,
        probes_used=len(pairs),
        event_id=scoring_event_id,
        event_started_at=scoring_event_started_at,
        consent_verified=settings.ai_consent_enforcement_enabled,
        allowlist_verified=allowlist_verified_now,
        qualification_expires_at=qualification_expires_at,
    )
    if scoring_intent_id is not None and not call_details:
        await db.rollback()
        raise llm.LLMError("scoring audit unavailable")
    try:
        await usage.record_physical_calls(
            db,
            user_id,
            score_operation,
            call_details=call_details,
            intent_id=scoring_intent_id,
        )
    except Exception as exc:
        await db.rollback()
        raise llm.LLMError("scoring audit unavailable") from exc

    if result.scoring_contract_version != session.scoring_contract_version:
        raise llm.LLMError("scorer returned the wrong contract version")
    if result.status == "follow_up" and len(pairs) >= llm.MAX_SCORED_FOLLOW_UPS:
        # Defense in depth, at the write site. Both parsers already refuse to
        # return a probe here, so reaching this line means a parser bug — and the
        # cap has to hold anyway: a prompt alone can never extend a session.
        raise llm.LLMError("scorer asked for a follow-up past the structural cap")

    # Take the same short session-row barrier used by PATCH /draft only after all
    # provider and audit work is finished. Probe 1 -> probe 2 can update/insert
    # only SessionProbe rows while every Session value (including an already-empty
    # draft) remains unchanged, so relying on an incidental ORM UPDATE would leave
    # a late turn-1 draft unordered with this transcript commit.
    locked_session = await _owned_session(db, session.id, for_update=True)
    if locked_session is None:  # pragma: no cover - the initial owned read found it
        raise HTTPException(status_code=404, detail="session not found")
    session = locked_session

    # Only now does the transcript move onto the ORM objects. If the provider or
    # contract validation failed above, the request leaves no dirty session state
    # for a later transaction to commit accidentally.
    if pending is not None:
        pending.answer = body.text
        db.add(pending)
    else:
        session.answer_text = body.text
    session.draft_text = ""

    if result.status == "follow_up":
        next_turn_index = len(probes) + 1
        db.add(
            SessionProbe(
                session_id=session.id,
                idx=next_turn_index,
                question=result.follow_up_question or "",
            )
        )
        # Still written, and still truthful: "a scored probe was issued in this
        # session". The count lives in `session_probes`; this stays the cheap flag.
        session.follow_up_used = True
        session.status = STATUS_AWAITING_FOLLOW_UP
        db.add(session)
        await db.commit()
        return FollowUpOut(
            question=result.follow_up_question or "", turn_index=next_turn_index
        )

    now = datetime.now(UTC)
    session.score = result.score
    session.accuracy = result.accuracy
    session.depth = result.depth
    session.boundaries = result.boundaries
    session.feedback = result.feedback
    session.status = STATUS_COMPLETE
    session.ended_at = now

    # Mastery signal, written for practice runs too — the score is real and the
    # card's history shows it. Only the schedule is held back below.
    card.last_score = result.score
    card.last_accuracy = result.accuracy
    card.last_depth = result.depth
    card.last_boundaries = result.boundaries
    card.last_score_contract_version = session.scoring_contract_version
    card.last_reviewed_at = now
    if result.mastery_summary:
        card.mastery_summary = result.mastery_summary
    card.updated_at = now

    if not session.practice:
        today = await local_today(db)
        ease, interval_days, repetitions, next_review_at = apply_sm2(
            card.ease_factor,
            card.interval_days,
            card.repetitions,
            # Gated on mechanism accuracy alone, from the FINAL session after any
            # follow-up. `ScoreResult` guarantees it is set on a complete result.
            quality_for(result.accuracy or 0),
            today,
        )
        card.ease_factor = ease
        card.interval_days = interval_days
        card.repetitions = repetitions
        card.next_review_at = next_review_at

    if (
        session.scoring_contract_version == SCORING_CONTRACT_V2
        and session.accuracy is not None
        and session.accuracy >= 3
    ):
        # Freeze the optional qualitative prompt beside the score and schedule.
        # Reconstructing it after commit from a later history count makes a lost
        # terminal response non-idempotent and can show a question different from
        # the one `/coaching` grades.
        completed_coaching = (
            await db.exec(
                select(func.count(Session.id)).where(
                    Session.card_id == session.card_id,
                    col(Session.coaching_answer).is_not(None),
                )
            )
        ).one()
        session.coaching_focus = next_coaching_focus(completed_coaching)
        session.coaching_question = coaching_question(session.coaching_focus)

    db.add(session)
    db.add(card)
    # Session and card land in a single transaction. A partial write here — answer
    # saved, SM-2 not applied — would leave the card permanently stuck.
    await db.commit()
    return _complete_response(session, card)


@router.post("/sessions/{session_id}/reattempt", response_model=ReattemptOut)
async def submit_reattempt(
    session_id: uuid.UUID, body: AnswerIn, db: AsyncSession = Depends(get_session)
) -> ReattemptOut:
    """Turn 3: a coached re-attempt, after the correction has already been given.

    This endpoint must never touch `ease_factor`, `interval_days`, `repetitions`, or
    `next_review_at`, and never touch `session.score` or the three axis columns. It
    runs *after* the session is complete and SM-2 is already applied — a turn that
    happens once the model has stated the correct mechanism measures coached
    performance, and feeding that to the scheduler would inflate the interval by the
    ease factor on exactly the cards just gotten wrong.

    See docs/multi-turn-coaching-design.md §4. Nothing here is load-bearing on the
    LLM behaving: the write set below is the guarantee.
    """
    session = await _owned_session(db, session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="session not found")
    if (
        session.status == STATUS_COMPLETE
        and session.reattempt_used
        and session.reattempt_answer == body.text
    ):
        # A committed turn needs no provider or consent check. Lock the card only
        # to prove its mastery summary has not since been replaced by a new review.
        card = await owned_card(db, session.card_id, for_update=True)
        if card is None:  # pragma: no cover — FK guarantees this
            raise HTTPException(status_code=404, detail="card not found")
        await db.refresh(session)
        if (
            session.status == STATUS_COMPLETE
            and session.reattempt_used
            and session.reattempt_answer == body.text
        ):
            if _card_was_reviewed_since(card, session):
                raise HTTPException(status_code=409, detail="card has been reviewed since")
            return ReattemptOut(mastery_summary=card.mastery_summary)
    if session.reattempt_used:
        raise HTTPException(status_code=409, detail="re-attempt already used")
    user_id = current_user_id()
    settings = get_settings()
    await ai_consent.require_ai_processing(db, user_id, settings)
    card = await owned_card(db, session.card_id, for_update=True)
    if card is None:  # pragma: no cover — FK guarantees this
        raise HTTPException(status_code=404, detail="card not found")
    await db.refresh(session)
    # A re-attempt only exists after a scored, completed session. Any other status
    # means turn 2 never landed, so there is no correction to re-attempt.
    if session.status != STATUS_COMPLETE:
        raise HTTPException(status_code=409, detail="session is not complete")
    if session.reattempt_used and session.reattempt_answer == body.text:
        if _card_was_reviewed_since(card, session):
            raise HTTPException(status_code=409, detail="card has been reviewed since")
        return ReattemptOut(mastery_summary=card.mastery_summary)
    if session.reattempt_used:
        raise HTTPException(status_code=409, detail="re-attempt already used")
    if not _reattempt_eligible(session):
        raise HTTPException(status_code=409, detail="session is not eligible for a re-attempt")
    # An empty body would spend the one re-attempt on nothing: the model scores it 0
    # and rewrites the card's mastery summary on that basis. The client already
    # guards this; the endpoint must too, because the write is irreversible.
    if not body.text.strip():
        raise HTTPException(status_code=422, detail="re-attempt text is empty")

    # The offer expires when the card is reviewed again. Turn 3 rewrites
    # `mastery_summary`, which is live context for the *next* `score_answer` — so a
    # re-attempt against a stale session would let an old, already-superseded
    # session's coaching overwrite a newer review's assessment, and that is the one
    # indirect route by which turn 3 could reach a future scheduling decision.
    if _card_was_reviewed_since(card, session):
        raise HTTPException(status_code=409, detail="card has been reviewed since")

    # Scored before anything is written, matching `submit_answer` — a failed call
    # leaves the row untouched and the client can retry. Unlike `submit_answer`,
    # nothing is at risk either way: the score is already banked and the schedule
    # already applied.
    await usage.ensure_available(
        db,
        user_id,
        "reattempt",
        settings,
        consent_boundary_locked=True,
    )
    result = await llm.score_reattempt(
        topic=card.topic,
        question_asked=session.question_asked,
        feedback_given=session.feedback,
        reattempt_answer=body.text,
        # `_reattempt_eligible` guarantees this is set and <= 2.
        unaided_accuracy=session.accuracy or 0,
        answer_basis=card.answer_basis or card.answer_anchor,
        answer_rubric=card.answer_rubric,
    )
    usage.record(db, user_id, "reattempt")

    session.reattempt_answer = body.text
    session.reattempt_accuracy = result.accuracy
    session.reattempt_used = True
    session.draft_text = ""

    # The entire card-side write. `last_score` and the three `last_*` axes stay put:
    # they describe the unaided attempt, which is what Coverage and the tiers mean.
    if result.mastery_summary:
        card.mastery_summary = result.mastery_summary
        card.updated_at = datetime.now(UTC)
        db.add(card)

    db.add(session)
    await db.commit()

    return ReattemptOut(mastery_summary=card.mastery_summary)


@router.post("/sessions/{session_id}/coaching", response_model=CoachingOut)
async def submit_coaching(
    session_id: uuid.UUID, body: AnswerIn, db: AsyncSession = Depends(get_session)
) -> CoachingOut:
    """One optional, qualitative post-result turn with a four-column write set."""
    session = await _owned_session(db, session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="session not found")
    replay = _replayed_coaching(session, body.text)
    if replay is not None:
        return replay
    if session.coaching_answer is not None:
        raise HTTPException(status_code=409, detail="qualitative coaching already used")
    user_id = current_user_id()
    settings = get_settings()
    await ai_consent.require_ai_processing(db, user_id, settings)
    # Card lock serializes completed coaching turns across separate sessions for
    # the same card, so two simultaneous submissions cannot select the same
    # alternation focus. It deliberately does not lock the session row: draft
    # persistence must stay cheap while the model is running.
    card = await owned_card(db, session.card_id, for_update=True)
    if card is None:  # pragma: no cover — FK guarantees this
        raise HTTPException(status_code=404, detail="card not found")
    await db.refresh(session)
    replay = _replayed_coaching(session, body.text)
    if replay is not None:
        return replay
    if session.status != STATUS_COMPLETE:
        raise HTTPException(status_code=409, detail="session is not complete")
    if session.scoring_contract_version != SCORING_CONTRACT_V2:
        raise HTTPException(status_code=409, detail="qualitative coaching requires V2")
    if session.accuracy is None or session.accuracy < 3:
        raise HTTPException(status_code=409, detail="failed Recall uses re-attempt coaching")
    if session.coaching_answer is not None:
        raise HTTPException(status_code=409, detail="qualitative coaching already used")
    if not body.text.strip():
        raise HTTPException(status_code=422, detail="coaching text is empty")

    if _card_was_reviewed_since(card, session):
        raise HTTPException(status_code=409, detail="card has been reviewed since")

    focus = session.coaching_focus
    question = session.coaching_question
    if focus is None or question is None:
        raise HTTPException(status_code=409, detail="qualitative coaching offer unavailable")

    await usage.ensure_available(
        db,
        user_id,
        "coaching",
        settings,
        consent_boundary_locked=True,
    )
    result = await llm.coach_answer(
        topic=card.topic,
        focus=focus,
        question=question,
        answer=body.text,
        answer_basis=card.answer_basis or card.answer_anchor,
        answer_rubric=card.answer_rubric,
    )
    usage.record(db, user_id, "coaching")

    session.coaching_answer = body.text
    session.coaching_feedback = result.feedback
    db.add(session)
    await db.commit()
    return CoachingOut(focus=focus, question=question, feedback=result.feedback)
