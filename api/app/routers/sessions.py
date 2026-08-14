import uuid
from collections.abc import Sequence
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy import func
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.auth import current_user_id
from app.config import get_settings
from app.db import get_session
from app.models import (
    CARD_ACTIVE,
    LIVE_STATUSES,
    STATUS_AWAITING_FOLLOW_UP,
    STATUS_COMPLETE,
    STATUS_OPEN,
    Card,
    Session,
    SessionProbe,
)
from app.routers.deps import as_utc, local_today
from app.schemas import (
    AnswerIn,
    CoachingOut,
    CompleteOut,
    DraftUpdate,
    FollowUpOut,
    ReattemptOut,
    SessionStart,
)
from app.services import llm, usage
from app.services.cards import recall_is_available
from app.services.scheduler import apply_sm2, quality_for
from app.services.scoring_contract import (
    SCORING_CONTRACT_V2,
    active_scoring_contract_version,
    coaching_question,
    next_coaching_focus,
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
    session: Session, probes: Sequence[SessionProbe], submitted_text: str
) -> FollowUpOut | None:
    """Return the already-committed probe for an exact replay of the last turn.

    The client deliberately retries a failed submission with the same saved text.
    If the server committed a follow-up but its response was lost, the session is
    already awaiting the next turn when that retry arrives. Treating the duplicate
    text as the *next* answer would let one piece of recall evidence occupy two
    scored turns. Returning the stored probe is both idempotent and free of another
    model call; a genuine new answer must differ from the turn it follows.

    The comparison is against the last *answered* turn — the initial answer while
    only probe 1 is outstanding, the previous probe's answer after that — so the
    guard covers every probe, not just the first.
    """
    pending = _pending_probe(session, probes)
    if pending is None or not pending.question:
        return None
    answered = probes[-2].answer if len(probes) >= 2 else session.answer_text
    if answered == submitted_text:
        return FollowUpOut(question=pending.question)
    return None


async def _resumed(db: AsyncSession, existing: Session) -> SessionStart:
    """Re-enter a live session at the turn it is actually waiting on.

    On a probe turn the displayed question is the pending probe's, not the card's
    — the same shared read both resume paths in `start_session` use, so a second
    probe cannot show the first one's text on one path and not the other.
    """
    is_follow_up = existing.status == STATUS_AWAITING_FOLLOW_UP
    question = existing.question_asked
    if is_follow_up:
        pending = _pending_probe(existing, await _session_probes(db, existing.id))
        question = pending.question if pending else ""
    return SessionStart(
        session_id=existing.id,
        question=question,
        is_follow_up=is_follow_up,
        draft_text=existing.draft_text,
        resumed=True,
    )


async def _live_session(db: AsyncSession, card_id: uuid.UUID) -> Session | None:
    return (
        await db.exec(
            select(Session)
            .where(Session.card_id == card_id, col(Session.status).in_(LIVE_STATUSES))
            .order_by(col(Session.started_at).desc())
        )
    ).first()


async def _owned_card(
    db: AsyncSession, card_id: uuid.UUID, *, for_update: bool = False
) -> Card | None:
    statement = select(Card).where(Card.id == card_id, Card.user_id == current_user_id())
    if for_update:
        statement = statement.with_for_update().execution_options(populate_existing=True)
    return (await db.exec(statement)).first()


async def _owned_session(db: AsyncSession, session_id: uuid.UUID) -> Session | None:
    return (
        await db.exec(
            select(Session)
            .join(Card, Card.id == Session.card_id)
            .where(Session.id == session_id, Card.user_id == current_user_id())
        )
    ).first()


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
    card = await _owned_card(db, card_id)
    if card is None:
        raise HTTPException(status_code=404, detail="card not found")
    if card.lifecycle_status != CARD_ACTIVE:
        raise HTTPException(status_code=409, detail="card is archived")
    _require_recall_available(card)

    existing = await _live_session(db, card_id)
    if existing is not None:
        # Returning the live session instead of creating a new one is what makes
        # resume work.
        return await _resumed(db, existing)

    question = card.canonical_question
    generated_question: str | None = None
    if not question:
        recent = (
            await db.exec(
                select(Session.question_asked)
                .where(Session.card_id == card_id)
                .order_by(col(Session.started_at).desc())
                .limit(RECENT_QUESTION_LIMIT)
            )
        ).all()

        await usage.ensure_available(db, current_user_id(), "question", get_settings())
        generated_question = await llm.generate_question(
            topic=card.topic,
            category=card.category,
            pattern=card.pattern,
            source_company=card.source_company,
            mastery_summary=card.mastery_summary,
            last_score=card.last_score,
            recent_questions=list(recent),
            answer_anchor=card.answer_anchor,
            source_excerpt=card.source_excerpt,
            answer_basis=card.answer_basis,
            answer_rubric=card.answer_rubric,
        )
        usage.record(db, current_user_id(), "question")

    # Serialize the final eligibility check with Learn. Question generation above
    # intentionally happens outside the lock; after any wait this populate-existing
    # read sees a learning exposure that landed while the provider was running.
    card = await _owned_card(db, card_id, for_update=True)
    if card is None:  # pragma: no cover - the initial owned read found it
        raise HTTPException(status_code=404, detail="card not found")
    if card.lifecycle_status != CARD_ACTIVE:
        raise HTTPException(status_code=409, detail="card is archived")
    _require_recall_available(card)

    # A concurrent starter may have committed while this request generated a
    # question. Resume its session rather than creating a second one.
    existing = await _live_session(db, card_id)
    if existing is not None:
        return await _resumed(db, existing)

    question = card.canonical_question or generated_question
    if not question:  # pragma: no cover - generation rejects an empty question
        raise llm.LLMError("question generation returned an empty question")
    if not card.canonical_question:
        # Persisted before it is returned, so the same question comes back next
        # time. Clearing this column by hand is the way to re-roll a bad one.
        card.canonical_question = question
        db.add(card)

    session = Session(
        card_id=card_id,
        question_asked=question,
        practice=practice,
        status=STATUS_OPEN,
        scoring_contract_version=active_scoring_contract_version(),
    )
    db.add(session)
    await db.commit()
    await db.refresh(session)

    return SessionStart(
        session_id=session.id,
        question=question,
        is_follow_up=False,
        draft_text="",
        resumed=False,
    )


@router.patch("/sessions/{session_id}/draft", status_code=204)
async def save_draft(
    session_id: uuid.UUID, body: DraftUpdate, db: AsyncSession = Depends(get_session)
) -> Response:
    """Cheap, idempotent, never blocked behind anything slow.

    Losing a spoken answer is the worst failure mode in the product, so this is
    deliberately a single indexed UPDATE with no LLM call and no validation
    beyond existence.
    """
    session = await _owned_session(db, session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="session not found")
    session.draft_text = body.draft_text
    db.add(session)
    await db.commit()
    return Response(status_code=204)


@router.post("/sessions/{session_id}/answers", response_model=FollowUpOut | CompleteOut)
async def submit_answer(
    session_id: uuid.UUID, body: AnswerIn, db: AsyncSession = Depends(get_session)
) -> FollowUpOut | CompleteOut:
    session = await _owned_session(db, session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="session not found")
    observed_status = session.status

    card = await _owned_card(db, session.card_id, for_update=True)
    if card is None:  # pragma: no cover — FK guarantees this
        raise HTTPException(status_code=404, detail="card not found")
    # The card lock serializes reviews without blocking PATCH /draft, which only
    # touches the session row. Refresh after any wait. An exact replay of the last
    # answered turn may observe the committed probe either before the lock or after
    # waiting on it; in both cases return that same probe without scoring again.
    await db.refresh(session)
    probes = await _session_probes(db, session.id)
    replay = _replayed_answer(session, probes, body.text)
    if replay is not None:
        return replay
    if session.status != observed_status:
        raise HTTPException(status_code=409, detail="session advanced during submission")
    if session.status == STATUS_COMPLETE:
        raise HTTPException(status_code=409, detail="session already complete")

    pending = _pending_probe(session, probes)
    if session.status == STATUS_AWAITING_FOLLOW_UP and pending is None:  # pragma: no cover
        # The status is only ever written beside an unanswered probe row, in the
        # same transaction. Refuse rather than score this answer as the initial one.
        raise HTTPException(status_code=409, detail="session has no pending probe")

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
    await usage.ensure_available(db, current_user_id(), score_operation, get_settings())
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
    )
    usage.record(db, current_user_id(), score_operation)

    if result.scoring_contract_version != session.scoring_contract_version:
        raise llm.LLMError("scorer returned the wrong contract version")
    if result.status == "follow_up" and len(pairs) >= llm.MAX_SCORED_FOLLOW_UPS:
        # Defense in depth, at the write site. Both parsers already refuse to
        # return a probe here, so reaching this line means a parser bug — and the
        # cap has to hold anyway: a prompt alone can never extend a session.
        raise llm.LLMError("scorer asked for a follow-up past the structural cap")

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
        db.add(
            SessionProbe(
                session_id=session.id,
                idx=len(probes) + 1,
                question=result.follow_up_question or "",
            )
        )
        # Still written, and still truthful: "a scored probe was issued in this
        # session". The count lives in `session_probes`; this stays the cheap flag.
        session.follow_up_used = True
        session.status = STATUS_AWAITING_FOLLOW_UP
        db.add(session)
        await db.commit()
        return FollowUpOut(question=result.follow_up_question or "")

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

    next_review, interval = card.next_review_at, card.interval_days
    if not session.practice:
        today = await local_today(db)
        ease, interval, repetitions, next_review = apply_sm2(
            card.ease_factor,
            card.interval_days,
            card.repetitions,
            # Gated on mechanism accuracy alone, from the FINAL session after any
            # follow-up. `ScoreResult` guarantees it is set on a complete result.
            quality_for(result.accuracy or 0),
            today,
        )
        card.ease_factor = ease
        card.interval_days = interval
        card.repetitions = repetitions
        card.next_review_at = next_review

    db.add(session)
    db.add(card)
    # Session and card land in a single transaction. A partial write here — answer
    # saved, SM-2 not applied — would leave the card permanently stuck.
    await db.commit()

    eligible = _reattempt_eligible(session)
    coaching_offered = (
        session.scoring_contract_version == SCORING_CONTRACT_V2
        and session.accuracy is not None
        and session.accuracy >= 3
    )
    focus = None
    question = None
    if coaching_offered:
        completed = (
            await db.exec(
                select(func.count(Session.id)).where(
                    Session.card_id == session.card_id,
                    col(Session.coaching_answer).is_not(None),
                )
            )
        ).one()
        focus = next_coaching_focus(completed)
        question = coaching_question(focus)
    return CompleteOut(
        score=result.score or 0,
        recall_score=result.accuracy or 0,
        scoring_contract_version=session.scoring_contract_version,
        feedback=result.feedback,
        next_review_at=next_review,
        interval_days=interval,
        practice=session.practice,
        reattempt_offered=eligible,
        reattempt_prompt=REATTEMPT_PREFACE + session.question_asked if eligible else None,
        coaching_offered=coaching_offered,
        coaching_focus=focus,
        coaching_question=question,
    )


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
    card = await _owned_card(db, session.card_id, for_update=True)
    if card is None:  # pragma: no cover — FK guarantees this
        raise HTTPException(status_code=404, detail="card not found")
    await db.refresh(session)
    # A re-attempt only exists after a scored, completed session. Any other status
    # means turn 2 never landed, so there is no correction to re-attempt.
    if session.status != STATUS_COMPLETE:
        raise HTTPException(status_code=409, detail="session is not complete")
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
    if card.last_reviewed_at is not None and session.ended_at is not None:
        if as_utc(card.last_reviewed_at) > as_utc(session.ended_at):
            raise HTTPException(status_code=409, detail="card has been reviewed since")

    # Scored before anything is written, matching `submit_answer` — a failed call
    # leaves the row untouched and the client can retry. Unlike `submit_answer`,
    # nothing is at risk either way: the score is already banked and the schedule
    # already applied.
    await usage.ensure_available(db, current_user_id(), "reattempt", get_settings())
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
    usage.record(db, current_user_id(), "reattempt")

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
    # Card lock serializes completed coaching turns across separate sessions for
    # the same card, so two simultaneous submissions cannot select the same
    # alternation focus. It deliberately does not lock the session row: draft
    # persistence must stay cheap while the model is running.
    card = await _owned_card(db, session.card_id, for_update=True)
    if card is None:  # pragma: no cover — FK guarantees this
        raise HTTPException(status_code=404, detail="card not found")
    await db.refresh(session)
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

    if card.last_reviewed_at is not None and session.ended_at is not None:
        if as_utc(card.last_reviewed_at) > as_utc(session.ended_at):
            raise HTTPException(status_code=409, detail="card has been reviewed since")

    completed = (
        await db.exec(
            select(func.count(Session.id)).where(
                Session.card_id == session.card_id,
                col(Session.coaching_answer).is_not(None),
            )
        )
    ).one()
    focus = next_coaching_focus(completed)
    question = coaching_question(focus)

    await usage.ensure_available(db, current_user_id(), "coaching", get_settings())
    result = await llm.coach_answer(
        topic=card.topic,
        focus=focus,
        question=question,
        answer=body.text,
        answer_basis=card.answer_basis or card.answer_anchor,
        answer_rubric=card.answer_rubric,
    )
    usage.record(db, current_user_id(), "coaching")

    session.coaching_focus = focus
    session.coaching_question = question
    session.coaching_answer = body.text
    session.coaching_feedback = result.feedback
    db.add(session)
    await db.commit()

    return CoachingOut(focus=focus, question=question, feedback=result.feedback)
