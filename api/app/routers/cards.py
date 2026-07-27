import uuid
from datetime import date, timedelta
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.db import get_session
from app.models import (
    DELIVERY_CONVERSATIONAL,
    LIVE_STATUSES,
    Card,
    Session,
)
from app.routers.deps import local_today
from app.schemas import (
    CardDetail,
    CardSummary,
    CreateCard,
    DueCard,
    Overview,
    SessionHistory,
    TierCard,
)
from app.services.cards import (
    COLD,
    SHAKY,
    TIERS,
    build_turns,
    classify_tier,
    days_since_review,
    due_label,
)

router = APIRouter(tags=["cards"])


def _summary(card: Card, today: date) -> CardSummary:
    """One card as the library sees it. Two fields are derived, not stored."""
    return CardSummary(
        **card.model_dump(),
        due_label=due_label(card.next_review_at, today),
        days_since_review=days_since_review(card, today),
    )


async def _resumable_card_ids(db: AsyncSession, card_ids: list[uuid.UUID]) -> set[uuid.UUID]:
    """A card is resumable if a live session holds a non-empty draft."""
    if not card_ids:
        return set()
    rows = await db.exec(
        select(Session.card_id).where(
            col(Session.card_id).in_(card_ids),
            col(Session.status).in_(LIVE_STATUSES),
            Session.draft_text != "",
        )
    )
    return set(rows.all())


@router.get("/cards/due", response_model=list[DueCard])
async def list_due(
    limit: int = Query(10, ge=1, le=100),
    db: AsyncSession = Depends(get_session),
) -> list[DueCard]:
    today = await local_today(db)
    cards = (
        await db.exec(
            select(Card)
            .where(
                Card.delivery_mode == DELIVERY_CONVERSATIONAL,
                col(Card.next_review_at) <= today,
            )
            .order_by(col(Card.next_review_at).asc(), col(Card.ease_factor).asc())
            .limit(limit)
        )
    ).all()

    resumable = await _resumable_card_ids(db, [c.id for c in cards])
    return [
        DueCard(
            id=c.id,
            topic=c.topic,
            category=c.category,
            mastery_summary=c.mastery_summary,
            last_score=c.last_score,
            due_label=due_label(c.next_review_at, today),
            resumable=c.id in resumable,
            missed_count=c.missed_count,
        )
        for c in cards
    ]


@router.get("/cards/overview", response_model=Overview)
async def overview(
    mode: Literal["conversational", "desk", "all"] = "all",
    db: AsyncSession = Depends(get_session),
) -> Overview:
    """Mastery classification across all cards — the desk-hour view."""
    today = await local_today(db)
    statement = select(Card)
    if mode != "all":
        statement = statement.where(Card.delivery_mode == mode)
    cards = (await db.exec(statement)).all()

    counts = dict.fromkeys(TIERS, 0)
    shaky: list[TierCard] = []
    cold: list[TierCard] = []

    for card in cards:
        tier = classify_tier(card, today)
        counts[tier] += 1
        if tier == SHAKY:
            shaky.append(
                TierCard(
                    id=card.id,
                    topic=card.topic,
                    mastery_summary=card.mastery_summary,
                    last_score=card.last_score,
                )
            )
        elif tier == COLD:
            cold.append(
                TierCard(
                    id=card.id,
                    topic=card.topic,
                    mastery_summary=card.mastery_summary,
                    days_overdue=(today - card.next_review_at).days,
                )
            )

    return Overview(counts=counts, shaky=shaky, cold=cold)


@router.get("/cards", response_model=list[CardSummary])
async def list_cards(
    sort: Literal["next_review", "weakest"] = "next_review",
    mode: Literal["conversational", "desk", "all"] = "all",
    db: AsyncSession = Depends(get_session),
) -> list[CardSummary]:
    """The whole library. Backs Review Sprint Setup and Coverage."""
    today = await local_today(db)
    statement = select(Card)
    if mode != "all":
        statement = statement.where(Card.delivery_mode == mode)
    if sort == "weakest":
        statement = statement.order_by(col(Card.ease_factor).asc(), col(Card.next_review_at).asc())
    else:
        statement = statement.order_by(col(Card.next_review_at).asc())
    return [_summary(c, today) for c in (await db.exec(statement)).all()]


@router.get("/cards/{card_id}", response_model=CardDetail)
async def get_card(card_id: uuid.UUID, db: AsyncSession = Depends(get_session)) -> CardDetail:
    card = await db.get(Card, card_id)
    if card is None:
        raise HTTPException(status_code=404, detail="card not found")

    today = await local_today(db)
    sessions = (
        await db.exec(
            select(Session)
            .where(Session.card_id == card_id)
            .order_by(col(Session.started_at).desc())
        )
    ).all()

    return CardDetail(
        **_summary(card, today).model_dump(),
        sessions=[
            SessionHistory(
                id=s.id,
                date=s.started_at,
                score=s.score,
                feedback=s.feedback,
                turns=build_turns(s),
            )
            for s in sessions
        ],
    )


@router.post("/cards", response_model=CardSummary, status_code=201)
async def create_card(body: CreateCard, db: AsyncSession = Depends(get_session)) -> CardSummary:
    today = await local_today(db)
    card = Card(
        topic=body.topic.strip(),
        category="Unsorted",
        delivery_mode=DELIVERY_CONVERSATIONAL,
        ease_factor=2.5,
        interval_days=1,
        repetitions=0,
        next_review_at=today if body.schedule == "now" else today + timedelta(days=1),
    )
    db.add(card)
    await db.commit()
    await db.refresh(card)
    return _summary(card, today)
