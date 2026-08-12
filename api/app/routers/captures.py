import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.auth import current_user_id
from app.config import get_settings
from app.db import get_session
from app.models import CAPTURE_ACTIVATED, Card, PendingCapture
from app.routers.cards import card_summary
from app.routers.deps import local_calendar
from app.schemas import (
    CaptureActivate,
    CaptureCreate,
    CaptureOut,
    CaptureSummary,
    CaptureUpdate,
    CardSummary,
)
from app.services import llm, usage
from app.services.card_lifecycle import (
    GroundingError,
    build_grounded_card,
    clean_rubric,
    grounding_from_capture,
    refresh_capture_status,
    storage_rubric,
)
from app.services.study_plan import normalize_topic, normalized_card_index

router = APIRouter(tags=["captures"])


def _out(capture: PendingCapture) -> CaptureOut:
    return CaptureOut(
        **capture.model_dump(exclude={"answer_rubric"}),
        # The Capture editor still speaks the legacy public keys. V2 is a
        # storage/scoring migration, not permission to silently change its wire.
        answer_rubric=clean_rubric(capture.answer_rubric),
    )


def _summary(
    *, id, topic, context, status, source_url, source_section, source_label,
    created_at, updated_at,
) -> CaptureSummary:
    return CaptureSummary(
        id=id,
        topic=topic,
        context=context,
        status=status,
        needs_source=not any(
            value.strip()
            for value in (source_url, source_section, source_label)
        ),
        created_at=created_at,
        updated_at=updated_at,
    )


async def _capture(db: AsyncSession, capture_id: uuid.UUID) -> PendingCapture:
    capture = (
        await db.exec(
            select(PendingCapture).where(
                PendingCapture.id == capture_id,
                PendingCapture.user_id == current_user_id(),
            )
        )
    ).first()
    if capture is None:
        raise HTTPException(status_code=404, detail="capture not found")
    return capture


@router.post("/captures", response_model=CaptureOut, status_code=201)
async def create_capture(
    body: CaptureCreate, db: AsyncSession = Depends(get_session)
) -> CaptureOut:
    capture = PendingCapture(
        user_id=current_user_id(),
        topic=body.topic.strip(),
        context=body.context.strip(),
    )
    db.add(capture)
    await db.commit()
    await db.refresh(capture)
    return _out(capture)


@router.get("/captures", response_model=list[CaptureSummary])
async def list_captures(
    include_activated: bool = Query(False),
    db: AsyncSession = Depends(get_session),
) -> list[CaptureSummary]:
    statement = select(
        PendingCapture.id,
        PendingCapture.topic,
        PendingCapture.context,
        PendingCapture.status,
        PendingCapture.source_url,
        PendingCapture.source_section,
        PendingCapture.source_label,
        PendingCapture.created_at,
        PendingCapture.updated_at,
    ).where(PendingCapture.user_id == current_user_id())
    if not include_activated:
        statement = statement.where(PendingCapture.status != CAPTURE_ACTIVATED)
    statement = statement.order_by(col(PendingCapture.created_at).desc())
    return [_summary(**row._mapping) for row in (await db.exec(statement)).all()]


@router.get("/captures/{capture_id}", response_model=CaptureOut)
async def get_capture(
    capture_id: uuid.UUID, db: AsyncSession = Depends(get_session)
) -> CaptureOut:
    return _out(await _capture(db, capture_id))


@router.patch("/captures/{capture_id}", response_model=CaptureOut)
async def update_capture(
    capture_id: uuid.UUID,
    body: CaptureUpdate,
    db: AsyncSession = Depends(get_session),
) -> CaptureOut:
    capture = await _capture(db, capture_id)
    if capture.status == CAPTURE_ACTIVATED:
        raise HTTPException(status_code=409, detail="capture is already activated")

    changes = body.model_dump(exclude_unset=True)
    rubric = changes.pop("answer_rubric", None)
    for name, value in changes.items():
        setattr(capture, name, (value or "").strip())
    if rubric is not None:
        capture.answer_rubric = storage_rubric(rubric)
    capture.updated_at = datetime.now(UTC)
    refresh_capture_status(capture)
    db.add(capture)
    await db.commit()
    await db.refresh(capture)
    return _out(capture)


@router.post("/captures/{capture_id}/question", response_model=CaptureOut)
async def prepare_question(
    capture_id: uuid.UUID,
    regenerate: bool = Query(False),
    db: AsyncSession = Depends(get_session),
) -> CaptureOut:
    capture = await _capture(db, capture_id)
    if capture.status == CAPTURE_ACTIVATED:
        raise HTTPException(status_code=409, detail="capture is already activated")
    if capture.canonical_question and not regenerate:
        return _out(capture)

    grounding = grounding_from_capture(capture)
    try:
        value = grounding.require_complete(require_question=False)
    except GroundingError as exc:
        raise HTTPException(
            status_code=409,
            detail={"code": "missing_grounding", "missing": exc.missing},
        ) from exc

    await usage.ensure_available(db, current_user_id(), "question", get_settings())
    capture.canonical_question = await llm.generate_question(
        topic=capture.topic,
        category="Unsorted",
        pattern=None,
        source_company=None,
        mastery_summary="",
        last_score=None,
        recent_questions=[],
        answer_basis=value.answer_basis,
        answer_rubric=value.answer_rubric,
    )
    usage.record(db, current_user_id(), "question")
    capture.updated_at = datetime.now(UTC)
    refresh_capture_status(capture)
    db.add(capture)
    await db.commit()
    await db.refresh(capture)
    return _out(capture)


@router.post("/captures/{capture_id}/activate", response_model=CardSummary, status_code=201)
async def activate_capture(
    capture_id: uuid.UUID,
    body: CaptureActivate,
    db: AsyncSession = Depends(get_session),
) -> CardSummary:
    capture = await _capture(db, capture_id)
    today, tz = await local_calendar(db)

    if capture.activated_card_id is not None:
        card = (
            await db.exec(
                select(Card).where(
                    Card.id == capture.activated_card_id,
                    Card.user_id == current_user_id(),
                )
            )
        ).first()
        if card is None:
            raise HTTPException(status_code=409, detail="activated card no longer exists")
        return card_summary(card, today, tz)

    try:
        card = build_grounded_card(
            user_id=current_user_id(),
            topic=capture.topic,
            category="Unsorted",
            grounding=grounding_from_capture(capture),
            today=today,
            schedule=body.schedule,
        )
    except GroundingError as exc:
        raise HTTPException(
            status_code=409,
            detail={"code": "missing_grounding", "missing": exc.missing},
        ) from exc

    duplicate = (await normalized_card_index(db, current_user_id())).get(
        normalize_topic(card.topic)
    )
    if duplicate is not None:
        raise HTTPException(
            status_code=409,
            detail={"code": "duplicate_card", "card_id": str(duplicate.id)},
        )

    db.add(card)
    await db.flush()
    capture.activated_card_id = card.id
    capture.status = CAPTURE_ACTIVATED
    capture.updated_at = datetime.now(UTC)
    db.add(capture)
    await db.commit()
    await db.refresh(card)
    return card_summary(card, today, tz)


@router.delete("/captures/{capture_id}", status_code=204)
async def discard_capture(
    capture_id: uuid.UUID, db: AsyncSession = Depends(get_session)
) -> Response:
    capture = await _capture(db, capture_id)
    if capture.status == CAPTURE_ACTIVATED:
        raise HTTPException(status_code=409, detail="activated captures cannot be discarded")
    await db.delete(capture)
    await db.commit()
    return Response(status_code=204)
