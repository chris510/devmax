import uuid
from collections.abc import Sequence
from datetime import UTC, date, datetime, tzinfo
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.exc import IntegrityError
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
    MaterialSource,
    Session,
    SessionProbe,
)
from app.routers.deps import (
    get_settings_row,
    local_calendar,
    local_today,
    now_in,
    owned_card,
)
from app.schemas import (
    CardDetail,
    CardGroundingUpdate,
    CardLearningOut,
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
    clean_rubric,
    recall_available_filter,
    restore,
    scoring_rubric,
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
    effective_review_date,
    learning_exposure_boundary,
)
from app.services.scoring_contract import project_card_score, project_session_score

router = APIRouter(tags=["cards"])


def _invalid_lineage() -> HTTPException:
    return HTTPException(
        status_code=409,
        detail={"code": "invalid_replacement_lineage"},
    )


async def _owned_card_links(
    db: AsyncSession,
) -> dict[uuid.UUID, tuple[uuid.UUID | None, uuid.UUID | None]]:
    rows = (
        await db.exec(
            select(Card.id, Card.replaces_card_id, Card.replaced_by_card_id).where(
                Card.user_id == current_user_id()
            )
        )
    ).all()
    return {
        card_id: (predecessor_id, successor_id)
        for card_id, predecessor_id, successor_id in rows
    }


def _lineage_root_id(
    card_id: uuid.UUID,
    links: dict[uuid.UUID, tuple[uuid.UUID | None, uuid.UUID | None]],
) -> uuid.UUID:
    """Find the immutable oldest card used as this lineage's transaction mutex."""
    seen: set[uuid.UUID] = set()
    cursor = card_id
    while True:
        if cursor in seen:
            raise _invalid_lineage()
        seen.add(cursor)
        link = links.get(cursor)
        if link is None:
            raise _invalid_lineage()
        predecessor_id = link[0]
        if predecessor_id is None:
            return cursor
        if predecessor_id not in links:
            raise _invalid_lineage()
        cursor = predecessor_id


def _lineage_component_ids(
    card_id: uuid.UUID,
    links: dict[uuid.UUID, tuple[uuid.UUID | None, uuid.UUID | None]],
) -> set[uuid.UUID]:
    """Return every card connected by either side of the bidirectional links."""
    adjacency: dict[uuid.UUID, set[uuid.UUID]] = {
        member_id: set() for member_id in links
    }
    for member_id, (predecessor_id, successor_id) in links.items():
        for linked_id in (predecessor_id, successor_id):
            if linked_id is None:
                continue
            if linked_id not in links:
                raise _invalid_lineage()
            adjacency[member_id].add(linked_id)
            adjacency[linked_id].add(member_id)

    component: set[uuid.UUID] = set()
    pending = [card_id]
    while pending:
        member_id = pending.pop()
        if member_id in component:
            continue
        component.add(member_id)
        pending.extend(adjacency[member_id] - component)
    return component


async def _owned_lineage_for_update(
    db: AsyncSession, card_id: uuid.UUID
) -> tuple[Card, list[Card]] | None:
    """Lock one replacement lineage from its stable root outwards.

    Every archive/restore/replace request takes the oldest card first. That row is
    the lineage mutex: a concurrent append cannot change the component while this
    transaction decides which member may be active. Remaining members are locked
    in UUID order for a deterministic secondary order. Reading links again after
    acquiring the root includes any replacement committed while this request was
    waiting.
    """
    links = await _owned_card_links(db)
    if card_id not in links:
        return None
    root_id = _lineage_root_id(card_id, links)
    root = await owned_card(db, root_id, for_update=True)
    if root is None:
        raise _invalid_lineage()

    links = await _owned_card_links(db)
    if card_id not in links or _lineage_root_id(card_id, links) != root_id:
        raise _invalid_lineage()
    component_ids = _lineage_component_ids(card_id, links)
    if root_id not in component_ids:
        raise _invalid_lineage()

    locked = {root_id: root}
    for member_id in sorted(component_ids - {root_id}, key=lambda value: value.bytes):
        member = await owned_card(db, member_id, for_update=True)
        if member is None:
            raise _invalid_lineage()
        locked[member_id] = member
    ordered_ids = sorted(locked, key=lambda value: value.bytes)
    return locked[card_id], [locked[member_id] for member_id in ordered_ids]


def _learning_basis(card: Card) -> str:
    return card.answer_basis.strip() or card.answer_anchor.strip()


def _learning_available(card: Card, *, has_linked_source: bool = False) -> bool:
    if card.lifecycle_status != CARD_ACTIVE:
        return False
    has_provenance = bool(
        card.source_url.strip()
        or card.source_section.strip()
        or card.source_label.strip()
        or card.source_excerpt.strip()
        or has_linked_source
    )
    rubric = scoring_rubric(card.answer_rubric)
    if card.answer_basis.strip():
        # First-party/curated cards cross the full grounding boundary: a source,
        # the approved basis, and all five teaching/scoring dimensions.
        return has_provenance and all(rubric.values())
    # Public imports intentionally have a smaller authority contract. A user-
    # confirmed anchor tied to that user's durable source is sufficient; they do
    # not manufacture a five-field rubric during import review.
    return bool(card.answer_anchor.strip()) and has_linked_source


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
        recall_not_before_at=card.recall_not_before_at,
        due_label=due_label(effective_review_date(card, tz), today),
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
        Card.recall_not_before_at,
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


def _session_history(session: Session, probes: Sequence[SessionProbe]) -> SessionHistory:
    score = project_session_score(session)
    return SessionHistory(
        id=session.id,
        date=session.started_at,
        score=session.score,
        recall_score=score.recall_score,
        legacy_composite_score=score.legacy_composite_score,
        scoring_contract_version=score.scoring_contract_version,
        feedback=session.feedback,
        turns=build_turns(session, probes),
        coaching_focus=session.coaching_focus,
        coaching_question=session.coaching_question,
        coaching_answer=session.coaching_answer,
        coaching_feedback=session.coaching_feedback,
    )


async def _probes_by_session(
    db: AsyncSession, session_ids: list[uuid.UUID]
) -> dict[uuid.UUID, list[SessionProbe]]:
    """Every scored probe on the card, in one query, grouped by session.

    A card's history is read whole, so loading probes per session would be an N+1
    that grows with how long the card has been reviewed. `idx` orders each group.
    """
    if not session_ids:
        return {}
    rows = (
        await db.exec(
            select(SessionProbe)
            .where(col(SessionProbe.session_id).in_(session_ids))
            .order_by(col(SessionProbe.idx))
        )
    ).all()
    grouped: dict[uuid.UUID, list[SessionProbe]] = {}
    for probe in rows:
        grouped.setdefault(probe.session_id, []).append(probe)
    return grouped


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
    today, tz = await local_calendar(db)
    now = datetime.now(UTC)
    cards = (
        await db.exec(
            select(Card)
            .options(_summary_columns())
            .where(
                Card.user_id == user_id,
                active_card_filter(),
                recall_available_filter(now),
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
                due_label=due_label(effective_review_date(card, tz), today),
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
    """Mastery classification across all cards: the desk-hour view."""
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
    card = await owned_card(db, card_id)
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
    probes = await _probes_by_session(db, [session.id for session in sessions])
    source_title = ""
    has_linked_source = False
    if card.source_id is not None:
        source_row = (
            await db.exec(
                select(MaterialSource.id, MaterialSource.title).where(
                    MaterialSource.id == card.source_id,
                    MaterialSource.user_id == current_user_id(),
                )
            )
        ).first()
        if source_row is not None:
            has_linked_source = True
            _, source_title = source_row

    return CardDetail(
        **card_summary(card, today, tz).model_dump(),
        # A saved/open answer owns the next honest action. Do not advertise Learn
        # while the POST would correctly refuse to expose the answer authority.
        learning_available=(
            _learning_available(card, has_linked_source=has_linked_source)
            and not any(session.status in LIVE_STATUSES for session in sessions)
        ),
        source_label=card.source_label or source_title,
        source_section=card.source_section,
        sessions=[
            _session_history(session, probes.get(session.id, ())) for session in sessions
        ],
    )


@router.post("/cards/{card_id}/learning", response_model=CardLearningOut)
async def open_learning(
    card_id: uuid.UUID, db: AsyncSession = Depends(get_session)
) -> CardLearningOut:
    """Expose trusted authority only after making same-session recall impossible.

    This is intentionally a POST. A GET that returned the answer before recording
    the hold could contaminate a score if the response arrived but the write did
    not. Every successful response counts as a fresh exposure and can only extend
    the existing hold; retrying a lost response is safe in that direction.
    """
    # Resolve/create the account's settings before taking the card lock. This
    # keeps singleton repair outside the card's short critical section while the
    # caller still owns the surrounding transaction.
    settings = await get_settings_row(db)
    card = await owned_card(db, card_id, for_update=True)
    if card is None:
        raise HTTPException(status_code=404, detail="card not found")
    if card.lifecycle_status != CARD_ACTIVE:
        raise HTTPException(status_code=409, detail={"code": "card_archived"})

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
            detail={
                "code": "live_session",
                "message": "finish the in-progress answer before opening learning material",
            },
        )

    basis = _learning_basis(card)
    source_title = ""
    has_linked_source = False
    if card.source_id is not None:
        source_row = (
            await db.exec(
                select(MaterialSource.id, MaterialSource.title).where(
                    MaterialSource.id == card.source_id,
                    MaterialSource.user_id == current_user_id(),
                )
            )
        ).first()
        if source_row is not None:
            has_linked_source = True
            _, source_title = source_row
    rubric = scoring_rubric(card.answer_rubric)
    if not _learning_available(card, has_linked_source=has_linked_source):
        raise HTTPException(
            status_code=409,
            detail={"code": "learning_material_unavailable"},
        )

    source_label = card.source_label.strip()
    if source_title and not source_label:
        source_label = source_title.strip()

    local_now = now_in(settings.timezone)
    exposed_at, recall_not_before_at = learning_exposure_boundary(
        local_now,
        existing_recall_not_before_at=card.recall_not_before_at,
    )
    card.last_learning_exposure_at = exposed_at
    card.recall_not_before_at = recall_not_before_at
    card.updated_at = exposed_at
    db.add(card)
    # Commit the gate before the response can reveal any answer authority.
    await db.commit()

    return CardLearningOut(
        card_id=card.id,
        topic=card.topic,
        category=card.category,
        source_url=card.source_url.strip(),
        source_section=card.source_section.strip(),
        source_label=source_label,
        source_excerpt=card.source_excerpt.strip(),
        core_explanation=basis,
        essential_account=rubric["essential_account"],
        acceptable_alternative=rubric["acceptable_alternative"],
        depth_extension=rubric["depth_extension"],
        boundary_extension=rubric["boundary_extension"],
        misconception=rubric["misconception"],
        recall_available_at=card.recall_not_before_at,
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
        # Storage switches to V2 aliases independently of the shipped iOS
        # maintenance editor. Keep its established wire vocabulary until that
        # client migrates, or a saved V2 card reopens with five blank fields.
        answer_rubric=clean_rubric(card.answer_rubric),
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
    card = await owned_card(db, card_id)
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
    # The same lock closes the session-start race for every maintenance write,
    # not only archive/replace. Authority and the canonical question are scoring
    # inputs and must never change underneath an answer already in progress.
    card = await owned_card(db, card_id, for_update=True)
    if card is None:
        raise HTTPException(status_code=404, detail="card not found")
    await _require_no_live_session(db, card)

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
    # Session creation takes the target-card lock for its final eligibility check
    # and insert. Lifecycle mutations additionally take the stable lineage root
    # first so restores and replacements of different members cannot both win.
    locked = await _owned_lineage_for_update(db, card_id)
    if locked is None:
        raise HTTPException(status_code=404, detail="card not found")
    card, _lineage = locked
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
    locked = await _owned_lineage_for_update(db, card_id)
    if locked is None:
        raise HTTPException(status_code=404, detail="card not found")
    card, lineage = locked
    conflict = next(
        (
            member
            for member in lineage
            if member.id != card.id and member.lifecycle_status == CARD_ACTIVE
        ),
        None,
    )
    if conflict is not None:
        # Keep the established error code for wire compatibility. It now means
        # any other active member, including an active predecessor.
        raise HTTPException(
            status_code=409,
            detail={
                "code": "replacement_is_active",
                "card_id": str(conflict.id),
            },
        )
    if card.lifecycle_status == CARD_ACTIVE:
        await db.commit()
        return _maintenance(card)
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
    # Resolve the local calendar before taking the lineage lock. A missing
    # settings singleton may be created here, outside the lifecycle critical
    # section, while remaining in this caller-owned transaction.
    today, tz = await local_calendar(db)
    # See `archive_card`: the lineage lock excludes restores/replacements of
    # related cards, and its target-card lock excludes session creation.
    locked = await _owned_lineage_for_update(db, card_id)
    if locked is None:
        raise HTTPException(status_code=404, detail="card not found")
    card, lineage = locked
    if card.lifecycle_status == CARD_ARCHIVED:
        raise HTTPException(status_code=409, detail="archived cards cannot be replaced")
    conflict = next(
        (
            member
            for member in lineage
            if member.id != card.id and member.lifecycle_status == CARD_ACTIVE
        ),
        None,
    )
    if conflict is not None:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "replacement_is_active",
                "card_id": str(conflict.id),
            },
        )
    successor = next(
        (
            member
            for member in lineage
            if member.replaces_card_id == card.id
            or member.id == card.replaced_by_card_id
        ),
        None,
    )
    if successor is not None:
        # Replacing a restored historical member would either fork the scalar
        # lineage or overwrite its existing successor. Restore the newest member
        # instead; history and both links remain intact.
        raise HTTPException(
            status_code=409,
            detail={
                "code": "newer_replacement_exists",
                "card_id": str(successor.id),
            },
        )
    await _require_no_live_session(db, card)

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
    try:
        await db.flush()
    except IntegrityError:
        # SQLite cannot honor the row lock above. The one-to-one lineage index
        # still picks a winner; report that winner as a normal conflict.
        await db.rollback()
        current = await owned_card(db, card_id)
        if current is None or current.replaced_by_card_id is None:
            raise
        raise HTTPException(
            status_code=409,
            detail={
                "code": "newer_replacement_exists",
                "card_id": str(current.replaced_by_card_id),
            },
        ) from None
    archive(card)
    card.replaced_by_card_id = replacement.id
    db.add(card)
    await db.commit()
    await db.refresh(replacement)
    return card_summary(replacement, today, tz)
