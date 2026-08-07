import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.auth import current_user_id
from app.config import get_settings
from app.db import get_session
from app.models import (
    LIVE_STATUSES,
    STATUS_AWAITING_FOLLOW_UP,
    STATUS_COMPLETE,
    STATUS_OPEN,
    Card,
    Session,
)
from app.routers.deps import as_utc, local_today
from app.schemas import (
    AnswerIn,
    CompleteOut,
    DraftUpdate,
    FollowUpOut,
    ReattemptOut,
    SessionStart,
)
from app.services import llm, usage
from app.services.scheduler import apply_sm2, quality_for

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


async def _live_session(db: AsyncSession, card_id: uuid.UUID) -> Session | None:
    return (
        await db.exec(
            select(Session)
            .where(Session.card_id == card_id, col(Session.status).in_(LIVE_STATUSES))
            .order_by(col(Session.started_at).desc())
        )
    ).first()


async def _owned_card(db: AsyncSession, card_id: uuid.UUID) -> Card | None:
    return (
        await db.exec(select(Card).where(Card.id == card_id, Card.user_id == current_user_id()))
    ).first()


async def _owned_session(db: AsyncSession, session_id: uuid.UUID) -> Session | None:
    return (
        await db.exec(
            select(Session)
            .join(Card, Card.id == Session.card_id)
            .where(Session.id == session_id, Card.user_id == current_user_id())
        )
    ).first()


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

    existing = await _live_session(db, card_id)
    if existing is not None:
        # Returning the live session instead of creating a new one is what makes
        # resume work.
        is_follow_up = existing.status == STATUS_AWAITING_FOLLOW_UP
        question = existing.follow_up_question or "" if is_follow_up else existing.question_asked
        return SessionStart(
            session_id=existing.id,
            question=question,
            is_follow_up=is_follow_up,
            draft_text=existing.draft_text,
            resumed=True,
        )

    question = card.canonical_question
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
        question = await llm.generate_question(
            topic=card.topic,
            category=card.category,
            pattern=card.pattern,
            source_company=card.source_company,
            mastery_summary=card.mastery_summary,
            last_score=card.last_score,
            recent_questions=list(recent),
            answer_anchor=card.answer_anchor,
            source_excerpt=card.source_excerpt,
        )
        usage.record(db, current_user_id(), "question")
        # Persisted before it is returned, so the same question comes back next
        # time. Clearing this column by hand is the way to re-roll a bad one.
        card.canonical_question = question
        db.add(card)

    session = Session(
        card_id=card_id, question_asked=question, practice=practice, status=STATUS_OPEN
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
    if session.status == STATUS_COMPLETE:
        raise HTTPException(status_code=409, detail="session already complete")

    card = await _owned_card(db, session.card_id)
    if card is None:  # pragma: no cover — FK guarantees this
        raise HTTPException(status_code=404, detail="card not found")

    answering_follow_up = session.status == STATUS_AWAITING_FOLLOW_UP
    if answering_follow_up:
        session.follow_up_answer = body.text
    else:
        session.answer_text = body.text
    session.draft_text = ""

    # Score before writing anything, so an LLM failure leaves the session and card
    # untouched rather than half-written. The answer is re-sent on retry.
    await usage.ensure_available(db, current_user_id(), "score", get_settings())
    result = await llm.score_answer(
        topic=card.topic,
        mastery_summary=card.mastery_summary,
        question_asked=session.question_asked,
        answer_text=session.answer_text,
        follow_up_question=session.follow_up_question,
        follow_up_answer=session.follow_up_answer,
        follow_up_used=session.follow_up_used,
        answer_anchor=card.answer_anchor,
        source_excerpt=card.source_excerpt,
    )
    usage.record(db, current_user_id(), "score")

    if result.status == "follow_up":
        session.follow_up_question = result.follow_up_question
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
    return CompleteOut(
        score=result.score or 0,
        feedback=result.feedback,
        next_review_at=next_review,
        interval_days=interval,
        practice=session.practice,
        reattempt_offered=eligible,
        reattempt_prompt=REATTEMPT_PREFACE + session.question_asked if eligible else None,
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

    card = await _owned_card(db, session.card_id)
    if card is None:  # pragma: no cover — FK guarantees this
        raise HTTPException(status_code=404, detail="card not found")

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
