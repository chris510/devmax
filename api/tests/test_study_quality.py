import asyncio
import uuid
from datetime import timedelta

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app import auth
from app.models import (
    CARD_ACTIVE,
    CARD_ARCHIVED,
    FOUNDER_USER_ID,
    Card,
    PendingCapture,
    Session,
)
from app.routers import captures as captures_router
from app.services import llm
from tests.conftest import API_HEADERS, TEST_DATABASE_URL, local_today, make_card

RUBRIC = {
    "mechanism": "A leader appends the entry locally before replication.",
    "acceptable_alternative": "Equivalent terminology for the replicated log is valid.",
    "trade_off": "Waiting for a quorum adds latency but preserves committed data.",
    "failure_mode": "A minority partition cannot commit new entries.",
    "misconception": "A local append alone does not make an entry committed.",
}


@pytest.fixture
def stub_question(monkeypatch):
    calls: list[dict] = []

    async def _question(**kwargs) -> str:
        calls.append(kwargs)
        return f"When is a Raft entry committed? ({len(calls)})"

    monkeypatch.setattr(llm, "generate_question", _question)
    return calls


async def _capture(client, topic: str = "Raft commit path") -> dict:
    response = await client.post(
        "/captures",
        headers=API_HEADERS,
        json={"topic": f"  {topic}  ", "context": "  missed this in a mock  "},
    )
    assert response.status_code == 201
    return response.json()


async def _ground(client, capture_id: str) -> dict:
    response = await client.patch(
        f"/captures/{capture_id}",
        headers=API_HEADERS,
        json={
            "source_url": "https://example.com/raft",
            "source_section": "Commitment",
            "source_label": "Raft paper",
            "answer_basis": "An entry commits after replication to a majority.",
            "answer_rubric": RUBRIC,
        },
    )
    assert response.status_code == 200
    return response.json()


async def test_capture_is_durable_but_never_a_card(client, db):
    capture = await _capture(client)

    assert capture["topic"] == "Raft commit path"
    assert capture["context"] == "missed this in a mock"
    assert capture["status"] == "pending_source"
    assert (await db.exec(select(PendingCapture))).one().id
    assert (await db.exec(select(Card))).all() == []
    assert (await client.get("/cards/due", headers=API_HEADERS)).json() == []
    assert (await client.get("/cards", headers=API_HEADERS)).json() == []


async def test_capture_rejects_empty_topic(client):
    response = await client.post("/captures", headers=API_HEADERS, json={"topic": ""})
    assert response.status_code == 422


async def test_activation_fails_closed_and_names_missing_grounding(client, db):
    capture = await _capture(client)

    response = await client.post(
        f"/captures/{capture['id']}/activate",
        headers=API_HEADERS,
        json={"schedule": "next"},
    )

    assert response.status_code == 409
    assert response.json()["detail"] == {
        "code": "missing_grounding",
        "missing": [
            "source",
            "answer_basis",
            "answer_rubric.mechanism",
            "answer_rubric.acceptable_alternative",
            "answer_rubric.trade_off",
            "answer_rubric.failure_mode",
            "answer_rubric.misconception",
            "canonical_question",
        ],
    }
    assert (await db.exec(select(Card))).all() == []


async def test_question_generation_is_grounded_idempotent_and_explicitly_regenerated(
    client, stub_question
):
    capture = await _capture(client)
    await _ground(client, capture["id"])
    first = await client.post(
        f"/captures/{capture['id']}/question", headers=API_HEADERS
    )
    replay = await client.post(
        f"/captures/{capture['id']}/question", headers=API_HEADERS
    )
    regenerated = await client.post(
        f"/captures/{capture['id']}/question?regenerate=true", headers=API_HEADERS
    )

    assert first.status_code == replay.status_code == regenerated.status_code == 200
    assert first.json()["status"] == "ready_to_review"
    assert replay.json()["canonical_question"] == first.json()["canonical_question"]
    assert regenerated.json()["canonical_question"] != first.json()["canonical_question"]
    assert len(stub_question) == 2
    assert stub_question[0]["answer_basis"].startswith("An entry commits")
    assert stub_question[0]["answer_rubric"] == RUBRIC


async def test_postgres_concurrent_capture_question_posts_transmit_once(
    client, db, monkeypatch
):
    if not TEST_DATABASE_URL.startswith("postgresql"):
        pytest.skip("capture question concurrency requires Postgres")

    capture = await _capture(client)
    await _ground(client, capture["id"])
    await db.rollback()
    engine = create_async_engine(TEST_DATABASE_URL)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    provider_entered = asyncio.Event()
    release_provider = asyncio.Event()
    calls = 0

    async def blocked_question(**_kwargs):
        nonlocal calls
        calls += 1
        provider_entered.set()
        await release_provider.wait()
        return "When is a Raft entry committed?"

    async def run_request():
        token = auth._current_user_id.set(FOUNDER_USER_ID)
        try:
            async with factory() as request_db:
                return await captures_router.prepare_question(
                    uuid.UUID(capture["id"]), False, request_db
                )
        finally:
            auth._current_user_id.reset(token)

    monkeypatch.setattr(llm, "generate_question", blocked_question)
    first = asyncio.create_task(run_request())
    second = None
    try:
        await asyncio.wait_for(provider_entered.wait(), timeout=3)
        second = asyncio.create_task(run_request())
        await asyncio.sleep(0.1)
        assert calls == 1
        assert not second.done()
    finally:
        release_provider.set()

    first_result = await asyncio.wait_for(first, timeout=3)
    second_result = await asyncio.wait_for(second, timeout=3)
    assert calls == 1
    assert second_result.canonical_question == first_result.canonical_question
    async with factory() as verify_db:
        stored = await verify_db.get(PendingCapture, uuid.UUID(capture["id"]))
        assert stored is not None
        assert stored.canonical_question == first_result.canonical_question
    await engine.dispose()


async def test_activation_is_atomic_and_replays_the_same_card(client, db, stub_question):
    capture = await _capture(client)
    await _ground(client, capture["id"])
    await client.post(f"/captures/{capture['id']}/question", headers=API_HEADERS)

    first = await client.post(
        f"/captures/{capture['id']}/activate",
        headers=API_HEADERS,
        json={"schedule": "next"},
    )
    replay = await client.post(
        f"/captures/{capture['id']}/activate",
        headers=API_HEADERS,
        json={"schedule": "next"},
    )

    assert first.status_code == replay.status_code == 201
    assert replay.json()["id"] == first.json()["id"]
    assert first.json()["due_label"] == "due tomorrow"
    assert len((await db.exec(select(Card))).all()) == 1
    stored = (await db.exec(select(PendingCapture))).one()
    assert stored.status == "activated"
    assert str(stored.activated_card_id) == first.json()["id"]
    assert (await client.get("/captures", headers=API_HEADERS)).json() == []


async def test_discard_removes_only_the_pending_capture(client, db):
    capture = await _capture(client)
    response = await client.delete(f"/captures/{capture['id']}", headers=API_HEADERS)

    assert response.status_code == 204
    assert (await db.exec(select(PendingCapture))).all() == []
    assert (await db.exec(select(Card))).all() == []


def _grounded_card(**overrides) -> Card:
    return make_card(
        source_url="https://example.com/raft",
        source_section="Commitment",
        source_label="Raft paper",
        answer_basis="An entry commits after replication to a majority.",
        answer_rubric=RUBRIC,
        canonical_question="When is a Raft log entry committed?",
        **overrides,
    )


async def test_archive_keeps_history_and_removes_card_from_active_selection(client, db):
    card = _grounded_card(last_score=4, repetitions=3)
    session = Session(
        card_id=card.id,
        question_asked=card.canonical_question or "",
        answer_text="After a quorum replicates it.",
        score=4,
        feedback="Correct.",
        status="complete",
    )
    db.add(card)
    db.add(session)
    await db.commit()

    before_schedule = (
        card.ease_factor,
        card.interval_days,
        card.repetitions,
        card.next_review_at,
    )
    response = await client.post(f"/cards/{card.id}/archive", headers=API_HEADERS)

    assert response.status_code == 200
    assert response.json()["lifecycle_status"] == CARD_ARCHIVED
    assert (await client.get("/cards/due", headers=API_HEADERS)).json() == []
    assert (await client.get("/cards", headers=API_HEADERS)).json() == []
    history = (await client.get(f"/cards/{card.id}", headers=API_HEADERS)).json()
    assert len(history["sessions"]) == 1
    await db.refresh(card)
    assert (
        card.ease_factor,
        card.interval_days,
        card.repetitions,
        card.next_review_at,
    ) == before_schedule


async def test_archived_card_cannot_start_a_session(client, db):
    card = _grounded_card(lifecycle_status=CARD_ARCHIVED)
    db.add(card)
    await db.commit()

    response = await client.post(f"/cards/{card.id}/sessions", headers=API_HEADERS)
    assert response.status_code == 409
    assert response.json()["detail"] == "card is archived"


async def test_legacy_grounding_preserves_schedule_and_accepts_the_existing_question(
    client, db
):
    card = make_card(
        canonical_question="How does a quorum commit work?",
        ease_factor=2.2,
        interval_days=11,
        repetitions=4,
        next_review_at=local_today() + timedelta(days=11),
        last_score=3,
    )
    db.add(card)
    await db.commit()
    before = (
        card.ease_factor,
        card.interval_days,
        card.repetitions,
        card.next_review_at,
        card.last_score,
    )

    response = await client.patch(
        f"/cards/{card.id}/grounding",
        headers=API_HEADERS,
        json={
            "source_url": "https://example.com/raft",
            "source_label": "Raft paper",
            "answer_basis": "An entry commits after replication to a majority.",
            "answer_rubric": RUBRIC,
            "canonical_question": card.canonical_question,
        },
    )

    assert response.status_code == 200
    await db.refresh(card)
    assert card.answer_rubric == RUBRIC
    assert (
        card.ease_factor,
        card.interval_days,
        card.repetitions,
        card.next_review_at,
        card.last_score,
    ) == before


async def test_legacy_question_with_history_must_be_replaced_not_rewritten(client, db):
    card = _grounded_card()
    db.add(card)
    db.add(
        Session(
            card_id=card.id,
            question_asked=card.canonical_question or "",
            score=3,
            status="complete",
        )
    )
    await db.commit()

    response = await client.patch(
        f"/cards/{card.id}/grounding",
        headers=API_HEADERS,
        json={"canonical_question": "A different question"},
    )

    assert response.status_code == 409
    assert response.json()["detail"].startswith("replace the card")
    await db.refresh(card)
    assert card.canonical_question == "When is a Raft log entry committed?"


async def test_replacement_preserves_old_history_and_starts_blank(client, db):
    card = _grounded_card(
        ease_factor=2.1,
        interval_days=18,
        repetitions=4,
        next_review_at=local_today() + timedelta(days=18),
        last_score=4,
    )
    old_session = Session(
        card_id=card.id,
        question_asked=card.canonical_question or "",
        score=4,
        feedback="Correct.",
        status="complete",
    )
    db.add(card)
    db.add(old_session)
    await db.commit()

    response = await client.post(
        f"/cards/{card.id}/replace",
        headers=API_HEADERS,
        json={
            "canonical_question": "A leader receives a write. When can it acknowledge?",
            "schedule": "now",
        },
    )

    assert response.status_code == 201
    replacement = await db.get(Card, uuid.UUID(response.json()["id"]))
    await db.refresh(card)
    assert replacement is not None
    assert card.lifecycle_status == CARD_ARCHIVED
    assert card.replaced_by_card_id == replacement.id
    assert replacement.lifecycle_status == CARD_ACTIVE
    assert replacement.replaces_card_id == card.id
    assert replacement.ease_factor == 2.5
    assert replacement.interval_days == 1
    assert replacement.repetitions == 0
    assert replacement.last_score is None
    assert (
        await db.exec(select(Session).where(Session.card_id == replacement.id))
    ).all() == []
    assert len((await db.exec(select(Session).where(Session.card_id == card.id))).all()) == 1


async def test_old_card_cannot_restore_while_its_replacement_is_active(client, db):
    old = _grounded_card(lifecycle_status=CARD_ARCHIVED)
    db.add(old)
    await db.flush()
    replacement = _grounded_card(replaces_card_id=old.id)
    db.add(replacement)
    await db.flush()
    old.replaced_by_card_id = replacement.id
    db.add(old)
    await db.commit()

    response = await client.post(f"/cards/{old.id}/restore", headers=API_HEADERS)

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "replacement_is_active"
