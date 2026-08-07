from datetime import UTC, datetime

from sqlmodel import select

from app.models import (
    FOUNDER_USER_ID,
    SOURCE_NEEDS_ATTENTION,
    SOURCE_PENDING,
    Card,
    MaterialSource,
    MaterialTopicProposal,
)
from app.services import llm, materials
from tests.conftest import API_HEADERS, GUIDE, make_card


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

    await materials._process_topics(db, source)
    source.status = SOURCE_NEEDS_ATTENTION
    db.add(source)
    await db.commit()

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

    await materials._process_plan(db, source)
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
    await materials._process_topics(db, source)
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
        await materials._process_topics(db, source)
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
    await materials._process_topics(db, current)
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
