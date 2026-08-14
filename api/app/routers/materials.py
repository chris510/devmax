import asyncio
import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Response
from sqlalchemy import func, update
from sqlalchemy.orm import defer
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.auth import current_user_id
from app.db import get_session
from app.models import (
    DELIVERY_CONVERSATIONAL,
    PROPOSAL_CLEAN,
    PROPOSAL_CONFIRMED,
    PROPOSAL_EXCLUDED,
    PROPOSAL_NEEDS_ATTENTION,
    SOURCE_CONFIRMED,
    SOURCE_FAILED,
    SOURCE_PENDING,
    Card,
    MaterialSource,
    MaterialTopicProposal,
)
from app.routers.deps import local_today
from app.schemas import (
    CollectionDetail,
    CollectionSummary,
    ManualMaterialIn,
    ManualTopicIn,
    MaterialConfirmIn,
    MaterialConfirmOut,
    MaterialImportIn,
    MaterialImportOut,
    MaterialTopicEdit,
    MaterialTopicOut,
)
from app.services import materials, study_plan

router = APIRouter(prefix="/materials", tags=["study material"])

COLLECTION = CollectionDetail(
    id="system-design-foundations",
    title="System design foundations",
    subtitle="Core mechanisms and design decisions for software-engineering interviews.",
    version="1.0",
    topic_count=6,
    sections=[
        "Request and data foundations",
        "Concrete technologies",
        "Patterns and application",
    ],
    source_note="Reviewed against the Devmax system-design curriculum.",
    topics=[
        ManualTopicIn(
            topic="Consistent hashing",
            answer_anchor=(
                "Hash keys and nodes onto a ring; virtual nodes balance ownership "
                "and limit movement when membership changes."
            ),
        ),
        ManualTopicIn(
            topic="Database replication",
            answer_anchor=(
                "Replicas copy an ordered write history; consistency, failover, and "
                "lag depend on acknowledgement and leadership rules."
            ),
        ),
        ManualTopicIn(
            topic="Cache invalidation",
            answer_anchor=(
                "Cached data needs an explicit freshness policy using expiry, "
                "versioning, or invalidation, with races handled at write boundaries."
            ),
        ),
        ManualTopicIn(
            topic="Rate limiting",
            answer_anchor=(
                "A limiter measures requests against a policy and rejects or delays "
                "excess work while accounting for distribution and clock boundaries."
            ),
        ),
        ManualTopicIn(
            topic="Message queues",
            answer_anchor=(
                "A queue decouples producers and consumers; delivery semantics, "
                "ordering, retries, and idempotency define correctness."
            ),
        ),
        ManualTopicIn(
            topic="Database indexing",
            answer_anchor=(
                "An index maintains an ordered access path that trades write and "
                "storage cost for selective lookup performance."
            ),
        ),
    ],
)


async def _owned_source(db: AsyncSession, source_id: uuid.UUID) -> MaterialSource:
    source = (
        await db.exec(
            select(MaterialSource).where(
                MaterialSource.id == source_id,
                MaterialSource.user_id == current_user_id(),
            )
        )
    ).first()
    if source is None:
        raise HTTPException(status_code=404, detail="material not found")
    return source


def _response_from_topics(
    source: MaterialSource,
    topics: list[MaterialTopicProposal],
    *,
    character_count: int | None = None,
) -> MaterialImportOut:
    summary = source.result_summary
    return MaterialImportOut(
        id=source.id,
        title=source.title,
        kind=source.kind,
        version=source.version,
        status=source.status,
        import_path=source.import_path,
        intent=source.intent,
        original_filename=source.original_filename,
        character_count=character_count if character_count is not None else len(source.source_text),
        clean_count=sum(row.status == PROPOSAL_CLEAN for row in topics),
        attention_count=sum(row.status == PROPOSAL_NEEDS_ATTENTION for row in topics),
        error=source.error,
        plan_draft_id=source.plan_draft_id,
        comparison={key: int(value) for key, value in (summary.get("comparison") or {}).items()},
        topics=[MaterialTopicOut.model_validate(row) for row in topics],
        created_at=source.created_at,
        updated_at=source.updated_at,
    )


async def _response(db: AsyncSession, source: MaterialSource) -> MaterialImportOut:
    topics = (
        await db.exec(
            select(MaterialTopicProposal)
            .where(MaterialTopicProposal.source_id == source.id)
            .order_by(MaterialTopicProposal.position)
        )
    ).all()
    return _response_from_topics(source, list(topics))


@router.post("/imports", response_model=MaterialImportOut, status_code=202)
async def start_import(
    body: MaterialImportIn,
    background: BackgroundTasks,
    db: AsyncSession = Depends(get_session),
) -> MaterialImportOut:
    previous = None
    if body.previous_version_id:
        previous = await _owned_source(db, body.previous_version_id)
    source = MaterialSource(
        user_id=current_user_id(),
        lineage_id=previous.lineage_id if previous else uuid.uuid4(),
        previous_version_id=previous.id if previous else None,
        version=previous.version + 1 if previous else 1,
        title=body.title.strip(),
        source_text=body.source_text,
        original_filename=body.original_filename,
        mime_type=body.mime_type,
        import_path=body.import_path,
        intent=body.intent,
        status=SOURCE_PENDING,
        requested_weeks=body.requested_weeks,
        weekly_capacity_minutes=body.weekly_capacity_minutes,
        mode=body.mode,
        deadline=body.deadline,
    )
    db.add(source)
    await db.commit()
    await db.refresh(source)
    background.add_task(materials.process_import, source.id)
    return await _response(db, source)


@router.get("/imports", response_model=list[MaterialImportOut])
async def list_imports(db: AsyncSession = Depends(get_session)) -> list[MaterialImportOut]:
    rows = (
        await db.exec(
            select(MaterialSource, func.length(MaterialSource.source_text))
            .options(defer(MaterialSource.source_text))
            .where(MaterialSource.user_id == current_user_id())
            .order_by(col(MaterialSource.updated_at).desc())
        )
    ).all()
    if not rows:
        return []
    topics = (
        await db.exec(
            select(MaterialTopicProposal)
            .where(col(MaterialTopicProposal.source_id).in_([source.id for source, _ in rows]))
            .order_by(MaterialTopicProposal.source_id, MaterialTopicProposal.position)
        )
    ).all()
    by_source: dict[uuid.UUID, list[MaterialTopicProposal]] = {}
    for topic in topics:
        by_source.setdefault(topic.source_id, []).append(topic)
    return [
        _response_from_topics(
            source,
            by_source.get(source.id, []),
            character_count=character_count or 0,
        )
        for source, character_count in rows
    ]


@router.get("/imports/{source_id}", response_model=MaterialImportOut)
async def get_import(
    source_id: uuid.UUID, db: AsyncSession = Depends(get_session)
) -> MaterialImportOut:
    return await _response(db, await _owned_source(db, source_id))


@router.post("/imports/{source_id}/retry", response_model=MaterialImportOut, status_code=202)
async def retry_import(
    source_id: uuid.UUID,
    background: BackgroundTasks,
    db: AsyncSession = Depends(get_session),
) -> MaterialImportOut:
    source = await _owned_source(db, source_id)
    retried_id = (
        await db.exec(
            update(MaterialSource)
            .where(
                MaterialSource.id == source.id,
                MaterialSource.user_id == current_user_id(),
                MaterialSource.status == SOURCE_FAILED,
            )
            .values(
                status=SOURCE_PENDING,
                processing_run_id=None,
                processing_heartbeat_at=None,
                error="",
                updated_at=datetime.now(UTC),
            )
            .returning(MaterialSource.id)
        )
    ).one_or_none()
    if retried_id is None:
        await db.rollback()
        raise HTTPException(status_code=409, detail="only a failed import can be retried")
    await db.commit()
    source = await db.get(MaterialSource, retried_id, populate_existing=True)
    assert source is not None
    background.add_task(materials.process_import, source.id)
    return await _response(db, source)


@router.patch("/topics/{proposal_id}", response_model=MaterialTopicOut)
async def edit_topic(
    proposal_id: uuid.UUID,
    body: MaterialTopicEdit,
    db: AsyncSession = Depends(get_session),
) -> MaterialTopicOut:
    row = (
        await db.exec(
            select(MaterialTopicProposal)
            .join(MaterialSource, MaterialSource.id == MaterialTopicProposal.source_id)
            .where(
                MaterialTopicProposal.id == proposal_id,
                MaterialSource.user_id == current_user_id(),
            )
        )
    ).first()
    if row is None:
        raise HTTPException(status_code=404, detail="topic not found")
    if body.topic is not None:
        row.topic = body.topic.strip()
    if body.answer_anchor is not None:
        row.answer_anchor = body.answer_anchor.strip()
    if body.action == "exclude":
        row.status = PROPOSAL_EXCLUDED
    elif body.action == "merge":
        if body.merge_into_id is None:
            raise HTTPException(status_code=422, detail="merge target required")
        target = (
            await db.exec(
                select(MaterialTopicProposal).where(
                    MaterialTopicProposal.id == body.merge_into_id,
                    MaterialTopicProposal.source_id == row.source_id,
                    MaterialTopicProposal.id != row.id,
                )
            )
        ).first()
        if target is None:
            raise HTTPException(status_code=404, detail="merge target not found")
        row.status = PROPOSAL_EXCLUDED
        row.merged_into_id = body.merge_into_id
    else:
        row.status = PROPOSAL_CLEAN if row.answer_anchor else PROPOSAL_NEEDS_ATTENTION
        row.issue = "" if row.answer_anchor else "A good answer anchor is required."
    row.updated_at = datetime.now(UTC)
    db.add(row)
    await db.commit()
    return MaterialTopicOut.model_validate(row)


@router.post("/imports/{source_id}/confirm", response_model=MaterialConfirmOut)
async def confirm_topics(
    source_id: uuid.UUID,
    body: MaterialConfirmIn,
    db: AsyncSession = Depends(get_session),
) -> MaterialConfirmOut:
    source = await _owned_source(db, source_id)
    rows = (
        await db.exec(
            select(MaterialTopicProposal).where(
                MaterialTopicProposal.source_id == source.id,
                col(MaterialTopicProposal.id).in_(body.selected_topic_ids),
            )
        )
    ).all()
    if len(rows) != len(set(body.selected_topic_ids)):
        raise HTTPException(status_code=404, detail="topic not found")
    if any(row.status != PROPOSAL_CLEAN or not row.answer_anchor.strip() for row in rows):
        raise HTTPException(status_code=409, detail="topics still need attention")
    existing = await study_plan.normalized_card_index(db, current_user_id())
    normalized = [study_plan.normalize_topic(row.topic) for row in rows]
    if len(set(normalized)) != len(normalized) or any(key in existing for key in normalized):
        raise HTTPException(status_code=409, detail="duplicate topic")
    today = await local_today(db)
    cards = [
        Card(
            user_id=current_user_id(),
            topic=row.topic,
            category=row.section_title or "Imported guide",
            delivery_mode=DELIVERY_CONVERSATIONAL,
            next_review_at=today,
            answer_anchor=row.answer_anchor,
            source_excerpt=row.source_excerpt,
            source_id=source.id,
        )
        for row in rows
    ]
    for card, row in zip(cards, rows, strict=True):
        db.add(card)
        row.status = PROPOSAL_CONFIRMED
        db.add(row)
    unselected = (
        await db.exec(
            select(MaterialTopicProposal).where(
                MaterialTopicProposal.source_id == source.id,
                col(MaterialTopicProposal.status).in_((PROPOSAL_CLEAN, PROPOSAL_NEEDS_ATTENTION)),
                col(MaterialTopicProposal.id).not_in(body.selected_topic_ids),
            )
        )
    ).all()
    for row in unselected:
        row.status = PROPOSAL_EXCLUDED
        db.add(row)
    await materials.confirm_source_version(db, source, current_user_id())
    await db.commit()
    return MaterialConfirmOut(source_id=source.id, created_card_ids=[card.id for card in cards])


async def _create_manual(
    db: AsyncSession, title: str, topics: list[ManualTopicIn], kind: str
) -> MaterialConfirmOut:
    source = MaterialSource(
        user_id=current_user_id(),
        kind=kind,
        title=title,
        source_text="\n\n".join(f"{item.topic}\n{item.answer_anchor}" for item in topics),
        status=SOURCE_CONFIRMED,
    )
    db.add(source)
    await db.flush()
    today = await local_today(db)
    existing = await study_plan.normalized_card_index(db, current_user_id())
    keys = [study_plan.normalize_topic(item.topic) for item in topics]
    if len(keys) != len(set(keys)) or any(key in existing for key in keys):
        raise HTTPException(status_code=409, detail="duplicate topic")
    cards = []
    for position, item in enumerate(topics, 1):
        proposal = MaterialTopicProposal(
            source_id=source.id,
            position=position,
            topic=item.topic.strip(),
            answer_anchor=item.answer_anchor.strip(),
            status=PROPOSAL_CONFIRMED,
        )
        card = Card(
            user_id=current_user_id(),
            topic=proposal.topic,
            category="Devmax collection" if kind == "collection" else "Manual topic",
            delivery_mode=DELIVERY_CONVERSATIONAL,
            next_review_at=today,
            answer_anchor=proposal.answer_anchor,
            source_id=source.id,
        )
        db.add(proposal)
        db.add(card)
        cards.append(card)
    await db.commit()
    return MaterialConfirmOut(source_id=source.id, created_card_ids=[card.id for card in cards])


@router.post("/manual", response_model=MaterialConfirmOut, status_code=201)
async def create_manual(
    body: ManualMaterialIn, db: AsyncSession = Depends(get_session)
) -> MaterialConfirmOut:
    return await _create_manual(db, body.title, body.topics, "manual")


@router.get("/collections", response_model=list[CollectionSummary])
async def collections() -> list[CollectionSummary]:
    return [
        CollectionSummary(**COLLECTION.model_dump(exclude={"sections", "source_note", "topics"}))
    ]


@router.get("/collections/{collection_id}", response_model=CollectionDetail)
async def collection(collection_id: str) -> CollectionDetail:
    if collection_id != COLLECTION.id:
        raise HTTPException(status_code=404, detail="collection not found")
    return COLLECTION


@router.post("/collections/{collection_id}", response_model=MaterialConfirmOut, status_code=201)
async def add_collection(
    collection_id: str, db: AsyncSession = Depends(get_session)
) -> MaterialConfirmOut:
    if collection_id != COLLECTION.id:
        raise HTTPException(status_code=404, detail="collection not found")
    return await _create_manual(db, COLLECTION.title, COLLECTION.topics, "collection")


@router.delete("/imports/{source_id}", status_code=204)
async def delete_import(source_id: uuid.UUID, db: AsyncSession = Depends(get_session)) -> Response:
    if not await materials.delete_source(
        db, source_id=source_id, user_id=current_user_id()
    ):
        raise HTTPException(status_code=404, detail="material not found")
    return Response(status_code=204)


async def schedule_pending_imports() -> list[asyncio.Task[None]]:
    return [
        asyncio.create_task(materials.resume_import(source_id), name=f"material-{source_id}")
        for source_id in await materials.resume_imports()
    ]
