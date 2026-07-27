import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.db import get_session
from app.models import (
    LIVE_STATUSES,
    STATUS_AWAITING_FOLLOW_UP,
    STATUS_COMPLETE,
    STATUS_OPEN,
    Card,
    Session,
)
from app.routers.deps import local_today
from app.schemas import AnswerIn, CompleteOut, DraftUpdate, FollowUpOut, SessionStart
from app.services import llm
from app.services.scheduler import apply_sm2, quality_for

router = APIRouter(tags=["sessions"])

RECENT_QUESTION_LIMIT = 3


async def _live_session(db: AsyncSession, card_id: uuid.UUID) -> Session | None:
    return (
        await db.exec(
            select(Session)
            .where(Session.card_id == card_id, col(Session.status).in_(LIVE_STATUSES))
            .order_by(col(Session.started_at).desc())
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
    card = await db.get(Card, card_id)
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

        question = await llm.generate_question(
            topic=card.topic,
            category=card.category,
            pattern=card.pattern,
            source_company=card.source_company,
            mastery_summary=card.mastery_summary,
            last_score=card.last_score,
            recent_questions=list(recent),
        )
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
    session = await db.get(Session, session_id)
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
    session = await db.get(Session, session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="session not found")
    if session.status == STATUS_COMPLETE:
        raise HTTPException(status_code=409, detail="session already complete")

    card = await db.get(Card, session.card_id)
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
    result = await llm.score_answer(
        topic=card.topic,
        mastery_summary=card.mastery_summary,
        question_asked=session.question_asked,
        answer_text=session.answer_text,
        follow_up_question=session.follow_up_question,
        follow_up_answer=session.follow_up_answer,
        follow_up_used=session.follow_up_used,
    )

    if result.status == "follow_up":
        session.follow_up_question = result.follow_up_question
        session.follow_up_used = True
        session.status = STATUS_AWAITING_FOLLOW_UP
        db.add(session)
        await db.commit()
        return FollowUpOut(question=result.follow_up_question or "")

    now = datetime.now(UTC)
    session.score = result.score
    session.mechanism_accuracy = result.mechanism_accuracy
    session.trade_off_awareness = result.trade_off_awareness
    session.failure_mode_awareness = result.failure_mode_awareness
    session.feedback = result.feedback
    session.status = STATUS_COMPLETE
    session.ended_at = now

    # Mastery signal, written for practice runs too — the score is real and the
    # card's history shows it. Only the schedule is held back below.
    card.last_score = result.score
    card.last_mechanism_accuracy = result.mechanism_accuracy
    card.last_trade_off_awareness = result.trade_off_awareness
    card.last_failure_mode_awareness = result.failure_mode_awareness
    card.last_reviewed_at = now
    if result.mastery_summary:
        card.mastery_summary = result.mastery_summary
    card.updated_at = now

    # Gated on mechanism accuracy alone, from the FINAL session after any follow-up.
    # A session scored before the decomposition shipped has no axis; falling back to
    # the composite is exact rather than approximate, because `derive_composite`
    # returns <= 2 for exactly the mechanism scores that fail. Defaulting to 0
    # instead would reset a card's interval on a shape mismatch.
    mechanism = result.mechanism_accuracy
    if mechanism is None:
        mechanism = result.score or 0

    next_review, interval = card.next_review_at, card.interval_days
    if not session.practice:
        today = await local_today(db)
        ease, interval, repetitions, next_review = apply_sm2(
            card.ease_factor,
            card.interval_days,
            card.repetitions,
            quality_for(mechanism),
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

    return CompleteOut(
        score=result.score or 0,
        feedback=result.feedback,
        next_review_at=next_review,
        interval_days=interval,
        practice=session.practice,
    )
