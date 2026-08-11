import uuid
from datetime import UTC, date, datetime, tzinfo
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import load_only
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.auth import current_user_id
from app.db import get_session
from app.models import (
    CARD_ACTIVE,
    CARD_ARCHIVED,
    DELIVERY_CONVERSATIONAL,
    LIVE_STATUSES,
    Card,
    Session,
)
from app.routers.deps import local_calendar, local_today
from app.schemas import (
    CardDetail,
    CardGroundingUpdate,
    CardMaintenance,
    CardSummary,
    DueCard,
    Overview,
    ReplaceCard,
    SessionHistory,
    TierCard,
)
from app.services.card_lifecycle import (
    Grounding,
    GroundingError,
    active_card_filter,
    archive,
    build_grounded_card,
    restore,
    storage_rubric,
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
from app.services.scoring_contract import project_card_score, project_session_score

router = APIRouter(tags=["cards"])


async def _owned_card(db: AsyncSession, card_id: uuid.UUID) -> Card | None:
    return (
        await db.exec(
            select(Card).where(Card.id == card_id, Card.user_id == current_user_id())
        )
    ).first()


def card_summary(card: Card, today: date, tz: tzinfo) -> CardSummary:
    """One card as the library sees it. Two fields are derived, not stored."""
    score = project_card_score(card)
    return CardSummary(
        id=card.id,
        topic=card.topic,
        category=card.category,
        delivery_mode=card.delivery_mode,
        mastery_summary=card.mastery_summary,
        last_score=card.last_score,
        recall_score=score.recall_score,
        score_kind=score.score_kind,
        scoring_contract_version=score.scoring_contract_version,
        last_accuracy=card.last_accuracy,
        last_depth=card.last_depth,
        last_boundaries=card.last_boundaries,
        ease_factor=card.ease_factor,
        interval_days=card.interval_days,
        repetitions=card.repetitions,
        next_review_at=card.next_review_at,
        due_label=due_label(card.next_review_at, today),
        days_since_review=days_since_review(card, today, tz),
        missed_count=card.missed_count,
        lifecycle_status=card.lifecycle_status,
    )


def _summary_columns():
    """Columns used by CardSummary, excluding large grounding payloads."""
    return load_only(
        Card.id,
        Card.topic,
        Card.category,
        Card.delivery_mode,
        Card.mastery_summary,
        Card.last_score,
        Card.last_accuracy,
        Card.last_depth,
        Card.last_boundaries,
        Card.last_score_contract_version,
        Card.ease_factor,
        Card.interval_days,
        Card.repetitions,
        Card.next_review_at,
        Card.last_reviewed_at,
        Card.missed_count,
        Card.lifecycle_status,
    )


def _overview_columns():
    """Columns used by mastery tiering, excluding large grounding payloads."""
    return load_only(
        Card.id,
        Card.topic,
        Card.delivery_mode,
        Card.mastery_summary,
        Card.last_score,
        Card.last_accuracy,
        Card.last_score_contract_version,
        Card.ease_factor,
        Card.interval_days,
        Card.repetitions,
        Card.next_review_at,
        Card.lifecycle_status,
    )


def _session_history(session: Session) -> SessionHistory:
    score = project_session_score(session)
    return SessionHistory(
        id=session.id,
        date=session.started_at,
        score=session.score,
        recall_score=score.recall_score,
        legacy_composite_score=score.legacy_composite_score,
        scoring_contract_version=score.scoring_contract_version,
        feedback=session.feedback,
        turns=build_turns(session),
        coaching_focus=session.coaching_focus,
        coaching_question=session.coaching_question,
        coaching_answer=session.coaching_answer,
        coaching_feedback=session.coaching_feedback,
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
    user_id = current_user_id()
    today = await local_today(db)
    cards = (
        await db.exec(
            select(Card)
            .options(_summary_columns())
            .where(
                Card.user_id == user_id,
                active_card_filter(),
                Card.delivery_mode == DELIVERY_CONVERSATIONAL,
                col(Card.next_review_at) <= today,
            )
            .order_by(col(Card.next_review_at).asc(), col(Card.ease_factor).asc())
            .limit(limit)
        )
    ).all()

    resumable = await _resumable_card_ids(db, [c.id for c in cards])
    result = []
    for card in cards:
        score = project_card_score(card)
        result.append(
            DueCard(
                id=card.id,
                topic=card.topic,
                category=card.category,
                mastery_summary=card.mastery_summary,
                last_score=card.last_score,
                recall_score=score.recall_score,
                score_kind=score.score_kind,
                scoring_contract_version=score.scoring_contract_version,
                due_label=due_label(card.next_review_at, today),
                resumable=card.id in resumable,
                missed_count=card.missed_count,
            )
        )
    return result


@router.get("/cards/overview", response_model=Overview)
async def overview(
    mode: Literal["conversational", "desk", "all"] = "all",
    db: AsyncSession = Depends(get_session),
) -> Overview:
    """Mastery classification across all cards — the desk-hour view."""
    today = await local_today(db)
    statement = (
        select(Card)
        .options(_overview_columns())
        .where(Card.user_id == current_user_id(), active_card_filter())
    )
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
            score = project_card_score(card)
            shaky.append(
                TierCard(
                    id=card.id,
                    topic=card.topic,
                    mastery_summary=card.mastery_summary,
                    last_score=card.last_score,
                    recall_score=score.recall_score,
                    score_kind=score.score_kind,
                    scoring_contract_version=score.scoring_contract_version,
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
    today, tz = await local_calendar(db)
    statement = (
        select(Card)
        .options(_summary_columns())
        .where(Card.user_id == current_user_id(), active_card_filter())
    )
    if mode != "all":
        statement = statement.where(Card.delivery_mode == mode)
    if sort == "weakest":
        statement = statement.order_by(col(Card.ease_factor).asc(), col(Card.next_review_at).asc())
    else:
        statement = statement.order_by(col(Card.next_review_at).asc())
    return [card_summary(c, today, tz) for c in (await db.exec(statement)).all()]


@router.get("/cards/{card_id}", response_model=CardDetail)
async def get_card(card_id: uuid.UUID, db: AsyncSession = Depends(get_session)) -> CardDetail:
    card = await _owned_card(db, card_id)
    if card is None:
        raise HTTPException(status_code=404, detail="card not found")

    today, tz = await local_calendar(db)
    sessions = (
        await db.exec(
            select(Session)
            .where(Session.card_id == card_id)
            .order_by(col(Session.started_at).desc())
        )
    ).all()

    return CardDetail(
        **card_summary(card, today, tz).model_dump(),
        sessions=[_session_history(session) for session in sessions],
    )


@router.post("/cards", status_code=409)
async def direct_card_creation_disabled() -> None:
    """Every new manual card crosses the grounding gate through Capture."""
    raise HTTPException(
        status_code=409,
        detail="direct card creation requires grounding; use /captures",
    )


def _maintenance(card: Card) -> CardMaintenance:
    return CardMaintenance(
        id=card.id,
        lifecycle_status=card.lifecycle_status,
        canonical_question=card.canonical_question or "",
        source_url=card.source_url,
        source_section=card.source_section,
        source_label=card.source_label,
        answer_basis=card.answer_basis,
        answer_rubric=card.answer_rubric,
        replaces_card_id=card.replaces_card_id,
        replaced_by_card_id=card.replaced_by_card_id,
    )


async def _require_no_live_session(db: AsyncSession, card: Card) -> None:
    live = (
        await db.exec(
            select(Session.id).where(
                Session.card_id == card.id,
                col(Session.status).in_(LIVE_STATUSES),
            )
        )
    ).first()
    if live is not None:
        raise HTTPException(
            status_code=409,
            detail="finish or abandon the in-progress answer before card maintenance",
        )


@router.get("/cards/{card_id}/maintenance", response_model=CardMaintenance)
async def get_maintenance(
    card_id: uuid.UUID, db: AsyncSession = Depends(get_session)
) -> CardMaintenance:
    card = await _owned_card(db, card_id)
    if card is None:
        raise HTTPException(status_code=404, detail="card not found")
    return _maintenance(card)


@router.patch("/cards/{card_id}/grounding", response_model=CardMaintenance)
async def update_card_grounding(
    card_id: uuid.UUID,
    body: CardGroundingUpdate,
    db: AsyncSession = Depends(get_session),
) -> CardMaintenance:
    """Add trusted authority to a legacy card without touching review state."""
    card = await _owned_card(db, card_id)
    if card is None:
        raise HTTPException(status_code=404, detail="card not found")

    changes = body.model_dump(exclude_unset=True)
    question = changes.pop("canonical_question", None)
    rubric = changes.pop("answer_rubric", None)
    if question is not None and question.strip() != (card.canonical_question or "").strip():
        has_history = (
            await db.exec(select(Session.id).where(Session.card_id == card.id).limit(1))
        ).first()
        if has_history is not None:
            raise HTTPException(
                status_code=409,
                detail="replace the card instead of changing a question with history",
            )
        card.canonical_question = question.strip()

    for name, value in changes.items():
        setattr(card, name, (value or "").strip())
    if rubric is not None:
        card.answer_rubric = storage_rubric(rubric)
    card.updated_at = datetime.now(UTC)
    db.add(card)
    await db.commit()
    await db.refresh(card)
    return _maintenance(card)


@router.post("/cards/{card_id}/archive", response_model=CardMaintenance)
async def archive_card(
    card_id: uuid.UUID, db: AsyncSession = Depends(get_session)
) -> CardMaintenance:
    card = await _owned_card(db, card_id)
    if card is None:
        raise HTTPException(status_code=404, detail="card not found")
    await _require_no_live_session(db, card)
    archive(card)
    db.add(card)
    await db.commit()
    await db.refresh(card)
    return _maintenance(card)


@router.post("/cards/{card_id}/restore", response_model=CardMaintenance)
async def restore_card(
    card_id: uuid.UUID, db: AsyncSession = Depends(get_session)
) -> CardMaintenance:
    card = await _owned_card(db, card_id)
    if card is None:
        raise HTTPException(status_code=404, detail="card not found")
    if card.lifecycle_status == CARD_ACTIVE:
        return _maintenance(card)
    if card.replaced_by_card_id is not None:
        replacement = await db.get(Card, card.replaced_by_card_id)
        if replacement is not None and replacement.lifecycle_status == CARD_ACTIVE:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "replacement_is_active",
                    "card_id": str(replacement.id),
                },
            )
    restore(card)
    db.add(card)
    await db.commit()
    await db.refresh(card)
    return _maintenance(card)


@router.post("/cards/{card_id}/replace", response_model=CardSummary, status_code=201)
async def replace_card(
    card_id: uuid.UUID,
    body: ReplaceCard,
    db: AsyncSession = Depends(get_session),
) -> CardSummary:
    card = await _owned_card(db, card_id)
    if card is None:
        raise HTTPException(status_code=404, detail="card not found")
    if card.lifecycle_status == CARD_ARCHIVED:
        raise HTTPException(status_code=409, detail="archived cards cannot be replaced")
    await _require_no_live_session(db, card)

    today, tz = await local_calendar(db)
    try:
        replacement = build_grounded_card(
            user_id=current_user_id(),
            topic=card.topic,
            category=card.category,
            grounding=Grounding(
                source_url=card.source_url,
                source_section=card.source_section,
                source_label=card.source_label,
                answer_basis=card.answer_basis,
                answer_rubric=card.answer_rubric,
                canonical_question=body.canonical_question,
            ),
            today=today,
            schedule=body.schedule,
            replaces_card_id=card.id,
        )
    except GroundingError as exc:
        raise HTTPException(
            status_code=409,
            detail={"code": "missing_grounding", "missing": exc.missing},
        ) from exc

    db.add(replacement)
    await db.flush()
    archive(card)
    card.replaced_by_card_id = replacement.id
    db.add(card)
    await db.commit()
    await db.refresh(replacement)
    return card_summary(replacement, today, tz)
