import asyncio
import uuid
from datetime import UTC, date, datetime

import pytest
from fastapi import HTTPException
from sqlalchemy import update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.models import (
    FOUNDER_USER_ID,
    SOURCE_FAILED,
    SOURCE_NEEDS_ATTENTION,
    SOURCE_PENDING,
    SOURCE_PROCESSING,
    SOURCE_READY,
    Card,
    MaterialSource,
    MaterialTopicProposal,
    StudyPlanGuideDraft,
)
from app.services import llm, materials, usage
from tests.conftest import (
    API_HEADERS,
    GUIDE,
    TEST_DATABASE_URL,
    import_payload,
    make_card,
)


async def _claim(db, source: MaterialSource) -> uuid.UUID:
    run_id = uuid.uuid4()
    source.status = "processing"
    source.processing_run_id = run_id
    source.processing_heartbeat_at = datetime.now(UTC)
    db.add(source)
    await db.commit()
    return run_id


async def test_import_saves_the_complete_source_before_background_work(client, db, monkeypatch):
    calls = []

    async def no_work(source_id):
        calls.append(source_id)

    monkeypatch.setattr(materials, "process_import", no_work)
    response = await client.post(
        "/materials/imports",
        headers=API_HEADERS,
        json={
            "title": "A 16-week law guide",
            "source_text": GUIDE,
            "original_filename": "law.md",
            "mime_type": "text/markdown",
            "import_path": "topics",
            "intent": "already_studied",
            "requested_weeks": 16,
            "weekly_capacity_minutes": 480,
        },
    )
    assert response.status_code == 202
    source = (await db.exec(select(MaterialSource))).one()
    assert source.source_text == GUIDE
    assert source.status == SOURCE_PENDING
    assert source.requested_weeks == 16
    assert calls == [source.id]


@pytest.mark.parametrize("status", [SOURCE_PENDING, SOURCE_PROCESSING, SOURCE_READY])
async def test_retry_rejects_every_import_that_is_not_failed(
    client, db, monkeypatch, status
):
    calls: list[uuid.UUID] = []

    async def no_work(source_id):
        calls.append(source_id)

    monkeypatch.setattr(materials, "process_import", no_work)
    source = MaterialSource(
        user_id=FOUNDER_USER_ID,
        title="Guide",
        source_text=GUIDE,
        status=status,
    )
    db.add(source)
    await db.commit()

    response = await client.post(
        f"/materials/imports/{source.id}/retry", headers=API_HEADERS
    )

    assert response.status_code == 409
    await db.refresh(source)
    assert source.status == status
    assert calls == []


async def test_retry_atomically_requeues_only_a_failed_import(
    client, db, monkeypatch
):
    calls: list[uuid.UUID] = []

    async def no_work(source_id):
        calls.append(source_id)

    monkeypatch.setattr(materials, "process_import", no_work)
    source = MaterialSource(
        user_id=FOUNDER_USER_ID,
        title="Guide",
        source_text=GUIDE,
        status=SOURCE_FAILED,
        error="provider failed",
        processing_run_id=uuid.uuid4(),
        processing_heartbeat_at=datetime.now(UTC),
    )
    db.add(source)
    await db.commit()

    response = await client.post(
        f"/materials/imports/{source.id}/retry", headers=API_HEADERS
    )

    assert response.status_code == 202
    await db.refresh(source)
    assert source.status == SOURCE_PENDING
    assert source.error == ""
    assert source.processing_run_id is None
    assert source.processing_heartbeat_at is None
    assert calls == [source.id]


async def test_delete_removes_source_owned_plan_draft_and_raw_guide(client, db):
    draft = StudyPlanGuideDraft(
        user_id=FOUNDER_USER_ID,
        guide_text=GUIDE,
        requested_weeks=4,
        weekly_capacity_minutes=720,
        start_date=date(2026, 8, 17),
        raw_response={"verbatim": GUIDE},
    )
    db.add(draft)
    await db.flush()
    source = MaterialSource(
        user_id=FOUNDER_USER_ID,
        title="Delete every transient copy",
        source_text=GUIDE,
        import_path="plan",
        plan_draft_id=draft.id,
        status=SOURCE_FAILED,
    )
    db.add(source)
    await db.commit()

    response = await client.delete(
        f"/materials/imports/{source.id}", headers=API_HEADERS
    )

    assert response.status_code == 204
    assert await db.get(MaterialSource, source.id) is None
    assert await db.get(StudyPlanGuideDraft, draft.id) is None


async def test_postgres_concurrent_workers_transmit_one_physical_import(
    db, monkeypatch
):
    if not TEST_DATABASE_URL.startswith("postgresql"):
        pytest.skip("atomic worker concurrency requires Postgres")

    source = MaterialSource(
        user_id=FOUNDER_USER_ID,
        title="Concurrent guide",
        source_text=GUIDE,
        import_path="topics",
        requested_weeks=4,
        weekly_capacity_minutes=720,
        status=SOURCE_PENDING,
    )
    db.add(source)
    await db.commit()

    engine = create_async_engine(TEST_DATABASE_URL)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr(materials, "session_factory", factory)
    entered = asyncio.Event()
    release = asyncio.Event()
    calls = 0

    async def blocked_import(**kwargs):
        nonlocal calls
        await kwargs["before_provider_call"](1)
        calls += 1
        entered.set()
        await release.wait()
        return import_payload()

    monkeypatch.setattr(llm, "import_guide", blocked_import)
    first = asyncio.create_task(materials.process_import(source.id))
    try:
        await asyncio.wait_for(entered.wait(), timeout=2)
        # The first worker is still at the provider; a duplicate task cannot
        # steal its fresh claim or make a second transmission.
        assert await asyncio.wait_for(materials.process_import(source.id), timeout=2) is False
        assert calls == 1
        release.set()
        assert await asyncio.wait_for(first, timeout=2) is True

        async with factory() as verify_db:
            current = await verify_db.get(MaterialSource, source.id)
            assert current is not None
            assert current.status in {SOURCE_READY, SOURCE_NEEDS_ATTENTION}
            assert current.processing_run_id is None
            assert current.processing_heartbeat_at is None
    finally:
        release.set()
        if not first.done():
            first.cancel()
            with pytest.raises(asyncio.CancelledError):
                await first
        await engine.dispose()


async def test_postgres_old_worker_discards_result_after_claim_token_changes(
    db, monkeypatch
):
    if not TEST_DATABASE_URL.startswith("postgresql"):
        pytest.skip("claim takeover concurrency requires Postgres")

    source = MaterialSource(
        user_id=FOUNDER_USER_ID,
        title="Claimed guide",
        source_text=GUIDE,
        import_path="topics",
        requested_weeks=4,
        weekly_capacity_minutes=720,
        status=SOURCE_PENDING,
    )
    db.add(source)
    await db.commit()

    engine = create_async_engine(TEST_DATABASE_URL)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr(materials, "session_factory", factory)
    entered = asyncio.Event()
    release = asyncio.Event()

    async def blocked_import(**kwargs):
        await kwargs["before_provider_call"](1)
        entered.set()
        await release.wait()
        return import_payload()

    monkeypatch.setattr(llm, "import_guide", blocked_import)
    old_worker = asyncio.create_task(materials.process_import(source.id))
    replacement_run_id = uuid.uuid4()
    try:
        await asyncio.wait_for(entered.wait(), timeout=2)
        async with factory() as control_db:
            await control_db.exec(
                update(MaterialSource)
                .where(MaterialSource.id == source.id)
                .values(
                    processing_run_id=replacement_run_id,
                    processing_heartbeat_at=datetime.now(UTC),
                )
            )
            await control_db.commit()

        release.set()
        assert await asyncio.wait_for(old_worker, timeout=2) is True

        async with factory() as verify_db:
            current = await verify_db.get(MaterialSource, source.id)
            proposals = (await verify_db.exec(select(MaterialTopicProposal))).all()
            assert current is not None
            assert current.status == SOURCE_PROCESSING
            assert current.processing_run_id == replacement_run_id
            assert proposals == []
    finally:
        release.set()
        if not old_worker.done():
            old_worker.cancel()
            with pytest.raises(asyncio.CancelledError):
                await old_worker
        await engine.dispose()


async def test_postgres_delete_before_authorization_prevents_material_transmission(
    db, monkeypatch
):
    if not TEST_DATABASE_URL.startswith("postgresql"):
        pytest.skip("provider-boundary deletion ordering requires Postgres")

    source = MaterialSource(
        user_id=FOUNDER_USER_ID,
        title="Deleted before transmission",
        source_text=GUIDE,
        import_path="plan",
        requested_weeks=4,
        weekly_capacity_minutes=720,
        status=SOURCE_PENDING,
    )
    db.add(source)
    await db.commit()

    engine = create_async_engine(TEST_DATABASE_URL)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr(materials, "session_factory", factory)
    boundary_entered = asyncio.Event()
    release_boundary = asyncio.Event()
    calls = 0
    real_ensure_available = usage.ensure_available

    async def paused_ensure_available(*args, **kwargs):
        boundary_entered.set()
        await release_boundary.wait()
        await real_ensure_available(*args, **kwargs)

    async def fake_import(**kwargs):
        nonlocal calls
        await kwargs["before_provider_call"](1)
        calls += 1
        return import_payload()

    monkeypatch.setattr(usage, "ensure_available", paused_ensure_available)
    monkeypatch.setattr(llm, "import_guide", fake_import)
    worker = asyncio.create_task(materials.process_import(source.id))
    try:
        await asyncio.wait_for(boundary_entered.wait(), timeout=3)
        async with factory() as delete_db:
            assert await materials.delete_source(
                delete_db,
                source_id=source.id,
                user_id=FOUNDER_USER_ID,
            )
        release_boundary.set()

        assert await asyncio.wait_for(worker, timeout=3) is True
        assert calls == 0
        async with factory() as verify_db:
            assert await verify_db.get(MaterialSource, source.id) is None
            assert (await verify_db.exec(select(StudyPlanGuideDraft))).all() == []
            assert (await verify_db.exec(select(MaterialTopicProposal))).all() == []
    finally:
        release_boundary.set()
        if not worker.done():
            worker.cancel()
            with pytest.raises(asyncio.CancelledError):
                await worker
        await engine.dispose()


async def test_postgres_authorization_before_delete_allows_only_inflight_call(
    db, monkeypatch
):
    if not TEST_DATABASE_URL.startswith("postgresql"):
        pytest.skip("provider-boundary deletion ordering requires Postgres")

    source = MaterialSource(
        user_id=FOUNDER_USER_ID,
        title="Deleted after authorization",
        source_text=GUIDE,
        import_path="plan",
        requested_weeks=4,
        weekly_capacity_minutes=720,
        status=SOURCE_PENDING,
    )
    db.add(source)
    await db.commit()

    engine = create_async_engine(TEST_DATABASE_URL)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr(materials, "session_factory", factory)
    provider_entered = asyncio.Event()
    release_provider = asyncio.Event()
    calls = 0

    async def blocked_import(**kwargs):
        nonlocal calls
        await kwargs["before_provider_call"](1)
        calls += 1
        provider_entered.set()
        await release_provider.wait()
        return import_payload()

    monkeypatch.setattr(llm, "import_guide", blocked_import)
    worker = asyncio.create_task(materials.process_import(source.id))
    try:
        await asyncio.wait_for(provider_entered.wait(), timeout=3)
        async with factory() as delete_db:
            assert await materials.delete_source(
                delete_db,
                source_id=source.id,
                user_id=FOUNDER_USER_ID,
            )
        release_provider.set()

        assert await asyncio.wait_for(worker, timeout=3) is True
        assert calls == 1
        async with factory() as verify_db:
            assert await verify_db.get(MaterialSource, source.id) is None
            assert (await verify_db.exec(select(StudyPlanGuideDraft))).all() == []
            assert (await verify_db.exec(select(MaterialTopicProposal))).all() == []
    finally:
        release_provider.set()
        if not worker.done():
            worker.cancel()
            with pytest.raises(asyncio.CancelledError):
                await worker
        await engine.dispose()


async def test_postgres_heartbeat_loss_cancels_work_before_material_retry(
    db, monkeypatch
):
    if not TEST_DATABASE_URL.startswith("postgresql"):
        pytest.skip("lease-loss cancellation requires Postgres")

    source = MaterialSource(
        user_id=FOUNDER_USER_ID,
        title="Lease-monitored guide",
        source_text=GUIDE,
        import_path="topics",
        requested_weeks=4,
        weekly_capacity_minutes=720,
        status=SOURCE_PENDING,
    )
    db.add(source)
    await db.commit()
    engine = create_async_engine(TEST_DATABASE_URL)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr(materials, "session_factory", factory)
    provider_entered = asyncio.Event()
    provider_cancelled = asyncio.Event()
    keep_provider_open = asyncio.Event()
    calls = 0
    active_calls = 0
    max_active_calls = 0

    async def import_with_first_call_blocked(**kwargs):
        nonlocal calls, active_calls, max_active_calls
        await kwargs["before_provider_call"](1)
        calls += 1
        active_calls += 1
        max_active_calls = max(max_active_calls, active_calls)
        try:
            if calls == 1:
                provider_entered.set()
                try:
                    await keep_provider_open.wait()
                except asyncio.CancelledError:
                    provider_cancelled.set()
                    raise
            return import_payload()
        finally:
            active_calls -= 1

    async def failed_heartbeat(_source_id, _run_id):
        await provider_entered.wait()
        raise HTTPException(
            status_code=503, detail="material import lease could not be renewed"
        )

    monkeypatch.setattr(llm, "import_guide", import_with_first_call_blocked)
    monkeypatch.setattr(materials, "_heartbeat_import", failed_heartbeat)
    try:
        assert await asyncio.wait_for(materials.process_import(source.id), timeout=3)
        await asyncio.wait_for(provider_cancelled.wait(), timeout=3)
        async with factory() as control_db:
            failed = await control_db.get(MaterialSource, source.id)
            assert failed is not None
            assert failed.status == SOURCE_FAILED
            failed.status = SOURCE_PENDING
            failed.processing_run_id = None
            failed.processing_heartbeat_at = None
            control_db.add(failed)
            await control_db.commit()

        never = asyncio.Event()

        async def stable_heartbeat(_source_id, _run_id):
            await never.wait()

        monkeypatch.setattr(materials, "_heartbeat_import", stable_heartbeat)
        assert await asyncio.wait_for(materials.process_import(source.id), timeout=3)
        assert calls == 2
        assert max_active_calls == 1
    finally:
        keep_provider_open.set()
        await engine.dispose()


async def test_topics_are_proposals_until_the_user_confirms(client, db, stub_import):
    source = MaterialSource(
        user_id=FOUNDER_USER_ID,
        title="Anatomy guide",
        source_text=GUIDE,
        import_path="topics",
        requested_weeks=4,
        weekly_capacity_minutes=720,
    )
    db.add(source)
    await db.commit()

    await materials._process_topics(db, source, await _claim(db, source))
    source.status = SOURCE_NEEDS_ATTENTION
    db.add(source)
    await db.commit()

    listing = (await client.get("/materials/imports", headers=API_HEADERS)).json()[0]
    detail = (await client.get(f"/materials/imports/{source.id}", headers=API_HEADERS)).json()
    assert listing["character_count"] == len(GUIDE)
    assert listing["clean_count"] == 4
    assert listing["attention_count"] == 1
    assert len(listing["topics"]) == 5
    assert len(detail["topics"]) == 5

    proposals = (
        await db.exec(
            select(MaterialTopicProposal)
            .where(MaterialTopicProposal.source_id == source.id)
            .order_by(MaterialTopicProposal.position)
        )
    ).all()
    assert len(proposals) == 5
    assert not (await db.exec(select(Card))).all()
    assert proposals[-1].status == "needs_attention"

    clean = [row.id for row in proposals if row.status == "clean"]
    confirmed = await client.post(
        f"/materials/imports/{source.id}/confirm",
        headers=API_HEADERS,
        json={"selected_topic_ids": [str(value) for value in clean]},
    )
    assert confirmed.status_code == 200, confirmed.text
    cards = (await db.exec(select(Card).order_by(Card.topic))).all()
    assert len(cards) == 4
    assert all(card.answer_anchor and card.source_id == source.id for card in cards)
    assert all(card.last_score is None for card in cards)


async def test_plan_import_reuses_its_preview_for_review_topics(db, stub_import):
    source = MaterialSource(
        user_id=FOUNDER_USER_ID,
        title="Anatomy study plan",
        source_text=GUIDE,
        import_path="plan",
        requested_weeks=4,
        weekly_capacity_minutes=720,
    )
    db.add(source)
    await db.commit()

    await materials._process_plan(db, source, await _claim(db, source))
    await db.commit()

    proposals = (
        await db.exec(
            select(MaterialTopicProposal)
            .where(MaterialTopicProposal.source_id == source.id)
            .order_by(MaterialTopicProposal.position)
        )
    ).all()
    assert stub_import.calls == 1
    assert len(proposals) == 5
    assert source.result_summary["clean_count"] == 4
    assert source.result_summary["attention_count"] == 1
    assert not (await db.exec(select(Card))).all()


async def test_attention_proposal_cannot_be_confirmed(client, db, stub_import):
    source = MaterialSource(
        user_id=FOUNDER_USER_ID,
        title="Study guide",
        source_text=GUIDE,
        import_path="topics",
        requested_weeks=4,
        weekly_capacity_minutes=720,
    )
    db.add(source)
    await db.commit()
    await materials._process_topics(db, source, await _claim(db, source))
    await db.commit()
    attention = (
        await db.exec(
            select(MaterialTopicProposal).where(
                MaterialTopicProposal.source_id == source.id,
                MaterialTopicProposal.status == "needs_attention",
            )
        )
    ).one()

    response = await client.post(
        f"/materials/imports/{source.id}/confirm",
        headers=API_HEADERS,
        json={"selected_topic_ids": [str(attention.id)]},
    )
    assert response.status_code == 409
    assert not (await db.exec(select(Card))).all()


async def test_manual_topic_requires_and_stores_a_trusted_answer_anchor(client, db):
    missing = await client.post(
        "/materials/manual",
        headers=API_HEADERS,
        json={"title": "Law", "topics": [{"topic": "Consideration", "answer_anchor": ""}]},
    )
    assert missing.status_code == 422

    created = await client.post(
        "/materials/manual",
        headers=API_HEADERS,
        json={
            "title": "Law",
            "topics": [
                {
                    "topic": "Consideration",
                    "answer_anchor": "A bargained-for exchange of legal value is required.",
                }
            ],
        },
    )
    assert created.status_code == 201
    card = (await db.exec(select(Card))).one()
    assert card.answer_anchor == "A bargained-for exchange of legal value is required."
    assert card.canonical_question is None


async def test_reviewed_collection_is_versioned_and_grounded(client, db):
    listing = await client.get("/materials/collections", headers=API_HEADERS)
    assert listing.status_code == 200
    assert listing.json()[0]["version"] == "1.0"

    added = await client.post(
        "/materials/collections/system-design-foundations", headers=API_HEADERS
    )
    assert added.status_code == 201
    cards = (await db.exec(select(Card))).all()
    assert len(cards) == 6
    assert all(card.answer_anchor for card in cards)


async def test_failed_extraction_does_not_mutate_the_saved_source_or_create_cards(db, stub_import):
    source = MaterialSource(
        user_id=FOUNDER_USER_ID,
        title="Medicine guide",
        source_text=GUIDE,
        import_path="topics",
        requested_weeks=4,
        weekly_capacity_minutes=720,
    )
    db.add(source)
    await db.commit()
    stub_import.error = llm.LLMError("provider unavailable")

    try:
        await materials._process_topics(db, source, await _claim(db, source))
    except llm.LLMError:
        pass
    else:
        raise AssertionError("the provider failure should reach the durable job wrapper")

    assert source.source_text == GUIDE
    assert not (await db.exec(select(Card))).all()
    assert not (await db.exec(select(MaterialTopicProposal))).all()


async def test_material_lookup_is_scoped_to_its_owner(client, db):
    from app.config import get_settings
    from app.models import Settings, User
    from app.services import authentication

    other = User()
    db.add(other)
    await db.flush()
    db.add(Settings(user_id=other.id))
    pair = await authentication.issue_session(db, other.id, get_settings())
    source = MaterialSource(
        user_id=other.id,
        title="Private anatomy source",
        source_text=GUIDE,
        import_path="topics",
        updated_at=datetime.now(UTC),
    )
    db.add(source)
    await db.commit()

    assert (
        await client.get(f"/materials/imports/{source.id}", headers=API_HEADERS)
    ).status_code == 404
    own = await client.get(
        f"/materials/imports/{source.id}",
        headers={"Authorization": f"Bearer {pair.access_token}"},
    )
    assert own.status_code == 200


async def test_a_new_source_version_reports_impact_without_changing_existing_cards(db, stub_import):
    previous = MaterialSource(
        user_id=FOUNDER_USER_ID,
        title="Guide",
        source_text=GUIDE,
        import_path="topics",
        status="confirmed",
    )
    db.add(previous)
    await db.flush()
    db.add(
        MaterialTopicProposal(
            source_id=previous.id,
            position=1,
            topic="Item L1",
            answer_anchor="The old anchor.",
            status="confirmed",
        )
    )
    db.add(
        MaterialTopicProposal(
            source_id=previous.id,
            position=2,
            topic="Removed topic",
            answer_anchor="Only in version one.",
            status="confirmed",
        )
    )
    existing = make_card(
        topic="Existing review",
        category="Imported guide",
        source_id=previous.id,
        answer_anchor="Keep this history intact.",
        last_score=4,
        repetitions=3,
        interval_days=12,
    )
    db.add(existing)
    current = MaterialSource(
        user_id=FOUNDER_USER_ID,
        title="Guide",
        source_text=GUIDE,
        import_path="topics",
        requested_weeks=4,
        weekly_capacity_minutes=720,
        previous_version_id=previous.id,
        lineage_id=previous.lineage_id,
        version=2,
    )
    db.add(current)
    await db.commit()

    before = (existing.last_score, existing.repetitions, existing.interval_days)
    await materials._process_topics(db, current, await _claim(db, current))
    await db.commit()
    await db.refresh(existing)

    assert current.result_summary["comparison"] == {
        "added": 4,
        "changed": 1,
        "removed": 1,
        "unchanged": 0,
    }
    assert (existing.last_score, existing.repetitions, existing.interval_days) == before
    assert previous.status == "confirmed"


async def test_merge_target_must_belong_to_the_same_source(client, db):
    first = MaterialSource(
        user_id=FOUNDER_USER_ID,
        title="First",
        source_text=GUIDE,
        import_path="topics",
    )
    second = MaterialSource(
        user_id=FOUNDER_USER_ID,
        title="Second",
        source_text=GUIDE,
        import_path="topics",
    )
    db.add(first)
    db.add(second)
    await db.flush()
    source_topic = MaterialTopicProposal(
        source_id=first.id, position=1, topic="One", answer_anchor="One anchor."
    )
    foreign_target = MaterialTopicProposal(
        source_id=second.id, position=1, topic="Two", answer_anchor="Two anchor."
    )
    db.add(source_topic)
    db.add(foreign_target)
    await db.commit()

    response = await client.patch(
        f"/materials/topics/{source_topic.id}",
        headers=API_HEADERS,
        json={"action": "merge", "merge_into_id": str(foreign_target.id)},
    )
    assert response.status_code == 404
    await db.refresh(source_topic)
    assert source_topic.status == "clean"
    assert source_topic.merged_into_id is None
