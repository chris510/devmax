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
    SOURCE_SUPERSEDED,
    STATUS_COMPLETE,
    Card,
    MaterialSource,
    MaterialTopicProposal,
    Session,
)
from app.routers.deps import local_today
from app.schemas import (
    CollectionDetail,
    CollectionSummary,
    LearningNoteConcept,
    LessonConceptProgress,
    LessonProgressOut,
    LessonQuizResult,
    ManualMaterialIn,
    ManualTopicIn,
    MaterialArtifactsOut,
    MaterialConfirmIn,
    MaterialConfirmOut,
    MaterialImportIn,
    MaterialImportOut,
    MaterialTopicEdit,
    MaterialTopicOut,
)
from app.services import materials, study_plan
from app.services.card_lifecycle import (
    Grounding,
    GroundingError,
    build_grounded_card,
)
from app.services.scoring_contract import project_card_score

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


def _artifacts_ready(source: MaterialSource) -> bool:
    return bool(
        source.distilled_at
        and source.canonical_note_markdown
        and source.recall_export_markdown
    )


def _response_from_topics(
    source: MaterialSource,
    topics: list[MaterialTopicProposal],
    *,
    character_count: int | None = None,
    artifacts_ready: bool | None = None,
) -> MaterialImportOut:
    summary = source.result_summary
    return MaterialImportOut(
        id=source.id,
        title=source.title,
        kind=source.kind,
        source_url=source.source_url,
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
        artifacts_ready=(
            _artifacts_ready(source) if artifacts_ready is None else artifacts_ready
        ),
        distilled_at=source.distilled_at,
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
        kind=body.kind,
        title=body.title.strip(),
        source_text=body.source_text,
        source_url=body.source_url,
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
            .options(
                defer(MaterialSource.source_text),
                defer(MaterialSource.canonical_note_markdown),
                defer(MaterialSource.recall_export_markdown),
            )
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
            artifacts_ready=source.distilled_at is not None,
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
    cards: list[Card] = []
    for row in rows:
        if source.import_path == "lesson":
            try:
                card = build_grounded_card(
                    user_id=current_user_id(),
                    topic=row.topic,
                    category=row.section_title or source.title,
                    grounding=Grounding(
                        source_url=source.source_url,
                        source_section=row.section_title,
                        source_label=source.title,
                        answer_basis=row.answer_anchor,
                        answer_rubric=row.answer_rubric,
                        canonical_question=row.canonical_question,
                    ),
                    today=today,
                    schedule="now",
                )
            except GroundingError as exc:
                raise HTTPException(
                    status_code=409,
                    detail={"code": "missing_grounding", "missing": exc.missing},
                ) from exc
            # Compatibility fields keep the existing learning/scoring fallback
            # available while the full grounding fields are the authority.
            card.answer_anchor = row.answer_anchor
            card.source_excerpt = row.source_excerpt
            card.source_id = source.id
        else:
            card = Card(
                user_id=current_user_id(),
                topic=row.topic,
                category=row.section_title or "Imported guide",
                delivery_mode=DELIVERY_CONVERSATIONAL,
                next_review_at=today,
                answer_anchor=row.answer_anchor,
                source_excerpt=row.source_excerpt,
                source_id=source.id,
            )
        cards.append(card)
    for card, row in zip(cards, rows, strict=True):
        db.add(card)
        row.card_id = card.id
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


async def _lesson_graph(
    db: AsyncSession, source: MaterialSource
) -> tuple[
    list[tuple[MaterialTopicProposal, Card]],
    dict[uuid.UUID, list[Session]],
]:
    if source.import_path != "lesson":
        raise HTTPException(status_code=409, detail={"code": "not_a_lesson"})
    if source.status not in {SOURCE_CONFIRMED, SOURCE_SUPERSEDED}:
        raise HTTPException(
            status_code=409,
            detail={"code": "lesson_not_confirmed"},
        )
    proposals = (
        await db.exec(
            select(MaterialTopicProposal)
            .where(
                MaterialTopicProposal.source_id == source.id,
                MaterialTopicProposal.status == PROPOSAL_CONFIRMED,
            )
            .order_by(MaterialTopicProposal.position)
        )
    ).all()
    if not proposals or any(row.card_id is None for row in proposals):
        raise HTTPException(
            status_code=409,
            detail={"code": "lesson_has_no_confirmed_cards"},
        )
    card_ids = [row.card_id for row in proposals if row.card_id is not None]
    cards = (
        await db.exec(
            select(Card).where(
                Card.user_id == current_user_id(),
                col(Card.id).in_(card_ids),
            )
        )
    ).all()
    by_id = {card.id: card for card in cards}
    if any(row.card_id not in by_id for row in proposals):
        raise HTTPException(
            status_code=409,
            detail={"code": "lesson_card_missing"},
        )
    sessions = (
        await db.exec(
            select(Session)
            .where(
                col(Session.card_id).in_(card_ids),
                Session.status == STATUS_COMPLETE,
                Session.practice == False,  # noqa: E712 - SQL expression
            )
            .order_by(Session.started_at)
        )
    ).all()
    sessions_by_card: dict[uuid.UUID, list[Session]] = {}
    for session in sessions:
        sessions_by_card.setdefault(session.card_id, []).append(session)
    return [(row, by_id[row.card_id]) for row in proposals], sessions_by_card


def _learning_note_concepts(
    source: MaterialSource,
    graph: list[tuple[MaterialTopicProposal, Card]],
    sessions_by_card: dict[uuid.UUID, list[Session]],
) -> list[LearningNoteConcept]:
    concepts: list[LearningNoteConcept] = []
    for proposal, card in graph:
        rubric = proposal.answer_rubric
        gotchas = [
            f"Alternative: {rubric.get('acceptable_alternative', '').strip()}",
            f"Trade-off: {rubric.get('trade_off', '').strip()}",
            f"Failure mode: {rubric.get('failure_mode', '').strip()}",
            f"Misconception: {rubric.get('misconception', '').strip()}",
        ]
        gotchas = [value for value in gotchas if value.rsplit(":", 1)[-1].strip()]
        quiz_results = [
            LessonQuizResult(
                reviewed_at=session.ended_at or session.started_at,
                question=session.question_asked[:2000],
                recall_score=session.accuracy,
                graded_summary=(session.feedback or card.mastery_summary)[:4000],
                feedback=session.feedback[:4000],
            )
            # The local writer intentionally caps durable quiz history at 20
            # rows per concept. Keep the newest unaided evidence at that boundary.
            for session in sessions_by_card.get(card.id, [])[-20:]
        ]
        latest_recall = quiz_results[-1].recall_score if quiz_results else None
        concepts.append(
            LearningNoteConcept(
                proposal_id=proposal.id,
                card_id=card.id,
                concept=proposal.topic,
                source_title=source.title,
                source_url=source.source_url,
                mental_model=proposal.answer_anchor,
                how_it_works=(
                    rubric.get("mechanism", "").strip() or proposal.answer_anchor
                ),
                gotchas=gotchas,
                recall_prompts=proposal.recall_questions,
                quiz_results=quiz_results,
                confidence=materials.confidence_for(latest_recall),
            )
        )
    return concepts


def _artifacts_response(
    source: MaterialSource, concepts: list[LearningNoteConcept]
) -> MaterialArtifactsOut:
    if not _artifacts_ready(source):
        raise HTTPException(
            status_code=409,
            detail={"code": "artifacts_not_ready"},
        )
    return MaterialArtifactsOut(
        source_id=source.id,
        title=source.title,
        source_url=source.source_url,
        distilled_at=source.distilled_at,
        canonical_note_markdown=source.canonical_note_markdown,
        recall_export_markdown=source.recall_export_markdown,
        concepts=concepts,
    )


@router.get("/imports/{source_id}/progress", response_model=LessonProgressOut)
async def lesson_progress(
    source_id: uuid.UUID, db: AsyncSession = Depends(get_session)
) -> LessonProgressOut:
    source = await _owned_source(db, source_id)
    graph, sessions_by_card = await _lesson_graph(db, source)
    concepts: list[LessonConceptProgress] = []
    weak_count = 0
    next_card_id: uuid.UUID | None = None
    for proposal, card in graph:
        projection = project_card_score(card)
        reviewed = bool(sessions_by_card.get(card.id))
        if not reviewed and next_card_id is None:
            next_card_id = card.id
        if projection.recall_score is not None and projection.recall_score <= 2:
            weak_count += 1
        concepts.append(
            LessonConceptProgress(
                proposal_id=proposal.id,
                card_id=card.id,
                concept=proposal.topic,
                mastery_summary=card.mastery_summary,
                last_score=card.last_score,
                recall_score=projection.recall_score,
                score_kind=projection.score_kind,
                scoring_contract_version=projection.scoring_contract_version,
                last_reviewed_at=card.last_reviewed_at,
                next_review_at=card.next_review_at,
                interval_days=card.interval_days,
            )
        )
    reviewed_count = sum(bool(sessions_by_card.get(card.id)) for _, card in graph)
    return LessonProgressOut(
        source_id=source.id,
        title=source.title,
        concept_count=len(graph),
        reviewed_count=reviewed_count,
        weak_count=weak_count,
        complete=reviewed_count == len(graph),
        next_card_id=next_card_id,
        concepts=concepts,
    )


@router.post("/imports/{source_id}/distill", response_model=MaterialArtifactsOut)
async def distill_lesson(
    source_id: uuid.UUID, db: AsyncSession = Depends(get_session)
) -> MaterialArtifactsOut:
    source = await _owned_source(db, source_id)
    graph, sessions_by_card = await _lesson_graph(db, source)
    reviewed_count = sum(bool(sessions_by_card.get(card.id)) for _, card in graph)
    if reviewed_count != len(graph):
        raise HTTPException(
            status_code=409,
            detail={
                "code": "lesson_incomplete",
                "reviewed_count": reviewed_count,
                "concept_count": len(graph),
            },
        )
    concepts = _learning_note_concepts(source, graph, sessions_by_card)
    note, recall = materials.render_lesson_markdown(
        source, [concept.model_dump(mode="python") for concept in concepts]
    )
    if (
        source.canonical_note_markdown != note
        or source.recall_export_markdown != recall
        or source.distilled_at is None
    ):
        source.canonical_note_markdown = note
        source.recall_export_markdown = recall
        source.distilled_at = datetime.now(UTC)
        source.updated_at = source.distilled_at
        db.add(source)
        await db.commit()
    return _artifacts_response(source, concepts)


@router.get("/imports/{source_id}/artifacts", response_model=MaterialArtifactsOut)
async def lesson_artifacts(
    source_id: uuid.UUID, db: AsyncSession = Depends(get_session)
) -> MaterialArtifactsOut:
    source = await _owned_source(db, source_id)
    if not _artifacts_ready(source):
        raise HTTPException(
            status_code=409,
            detail={"code": "artifacts_not_ready"},
        )
    graph, sessions_by_card = await _lesson_graph(db, source)
    return _artifacts_response(
        source, _learning_note_concepts(source, graph, sessions_by_card)
    )


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
        proposal.card_id = card.id
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
