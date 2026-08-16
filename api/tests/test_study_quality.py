import asyncio
import uuid
from datetime import timedelta

import pytest
from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from app import auth
from app.db import engine_kwargs
from app.models import (
    CARD_ACTIVE,
    CARD_ARCHIVED,
    FOUNDER_USER_ID,
    STATUS_OPEN,
    Card,
    PendingCapture,
    Session,
    Settings,
)
from app.routers import captures as captures_router
from app.routers import cards as cards_router
from app.routers import sessions as sessions_router
from app.schemas import CaptureActivate, CardGroundingUpdate, ReplaceCard
from app.services import llm
from tests.conftest import (
    API_HEADERS,
    TEST_DATABASE_URL,
    TEST_ON_POSTGRES,
    local_today,
    make_card,
)

RUBRIC = {
    "mechanism": "A leader appends the entry locally before replication.",
    "acceptable_alternative": "Equivalent terminology for the replicated log is valid.",
    "trade_off": "Waiting for a quorum adds latency but preserves committed data.",
    "failure_mode": "A minority partition cannot commit new entries.",
    "misconception": "A local append alone does not make an entry committed.",
}


@pytest.fixture
async def postgres_session_factory(db):
    if not TEST_DATABASE_URL.startswith("postgresql"):
        pytest.skip("row-lock concurrency requires Postgres")
    await db.rollback()
    engine = create_async_engine(TEST_DATABASE_URL)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        yield factory
    finally:
        await engine.dispose()


async def _as_founder(call):
    token = auth._current_user_id.set(FOUNDER_USER_ID)
    try:
        return await call
    finally:
        auth._current_user_id.reset(token)


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
    for topic in ("", "   "):
        response = await client.post(
            "/captures", headers=API_HEADERS, json={"topic": topic}
        )
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


async def test_postgres_concurrent_activation_replays_exactly_one_card(
    client, db, stub_question, postgres_session_factory
):
    capture = await _capture(client)
    await _ground(client, capture["id"])
    await client.post(f"/captures/{capture['id']}/question", headers=API_HEADERS)
    await db.rollback()
    capture_id = uuid.UUID(capture["id"])

    async def activate():
        async with postgres_session_factory() as request_db:
            return await _as_founder(
                captures_router.activate_capture(
                    capture_id,
                    CaptureActivate(schedule="next"),
                    request_db,
                )
            )

    first, second = await asyncio.gather(activate(), activate())
    assert first.id == second.id
    async with postgres_session_factory() as verify_db:
        cards = (await verify_db.exec(select(Card))).all()
        stored = await verify_db.get(PendingCapture, capture_id)
        assert len(cards) == 1
        assert stored is not None
        assert stored.activated_card_id == cards[0].id


async def test_postgres_normalized_topic_creation_is_serialized_across_captures(
    client, db, stub_question, postgres_session_factory
):
    first = await _capture(client, "Raft / Commit")
    second = await _capture(client, "  raft-commit  ")
    for capture in (first, second):
        await _ground(client, capture["id"])
        await client.post(f"/captures/{capture['id']}/question", headers=API_HEADERS)
    await db.rollback()

    async def activate(capture_id: str):
        async with postgres_session_factory() as request_db:
            return await _as_founder(
                captures_router.activate_capture(
                    uuid.UUID(capture_id),
                    CaptureActivate(schedule="next"),
                    request_db,
                )
            )

    results = await asyncio.gather(
        activate(first["id"]), activate(second["id"]), return_exceptions=True
    )
    successes = [result for result in results if not isinstance(result, Exception)]
    conflicts = [result for result in results if isinstance(result, HTTPException)]
    assert len(successes) == len(conflicts) == 1
    assert conflicts[0].status_code == 409
    assert conflicts[0].detail["code"] == "duplicate_card"
    async with postgres_session_factory() as verify_db:
        assert len((await verify_db.exec(select(Card))).all()) == 1


async def test_database_rejects_a_second_live_session_for_one_card(db):
    card = _grounded_card()
    db.add(card)
    db.add(Session(card_id=card.id, question_asked="first", status="open"))
    db.add(Session(card_id=card.id, question_asked="second", status="open"))
    with pytest.raises(IntegrityError):
        await db.commit()
    await db.rollback()


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


async def _replacement_chain(db, *statuses: str) -> list[Card]:
    cards = [_grounded_card(lifecycle_status=status) for status in statuses]
    for index, card in enumerate(cards):
        db.add(card)
        await db.flush()
        if index:
            predecessor = cards[index - 1]
            card.replaces_card_id = predecessor.id
            predecessor.replaced_by_card_id = card.id
            db.add(card)
            db.add(predecessor)
            await db.flush()
    await db.commit()
    return cards


@pytest.mark.parametrize(
    ("method", "endpoint", "payload"),
    [
        ("POST", "archive", None),
        (
            "POST",
            "replace",
            {
                "canonical_question": "A leader receives a write. When can it acknowledge?",
                "schedule": "now",
            },
        ),
        ("PATCH", "grounding", {"answer_basis": "A changed authority."}),
    ],
)
async def test_card_maintenance_rejects_an_existing_live_session(
    client, db, method, endpoint, payload
):
    """The maintenance/live-answer exclusion remains covered on SQLite too."""
    card = _grounded_card()
    db.add(card)
    db.add(
        Session(
            card_id=card.id,
            question_asked=card.canonical_question or "",
            status=STATUS_OPEN,
        )
    )
    await db.commit()

    response = await client.request(
        method,
        f"/cards/{card.id}/{endpoint}",
        headers=API_HEADERS,
        json=payload,
    )

    assert response.status_code == 409
    assert response.json()["detail"].startswith("finish or abandon")
    await db.refresh(card)
    assert card.lifecycle_status == CARD_ACTIVE
    assert len((await db.exec(select(Card))).all()) == 1


@pytest.mark.skipif(not TEST_ON_POSTGRES, reason="row-lock concurrency requires Postgres")
@pytest.mark.parametrize("maintenance_action", ["archive", "replace", "grounding"])
async def test_card_maintenance_serializes_with_concurrent_session_start(
    db, monkeypatch, maintenance_action
):
    """A checked-then-paused maintenance request owns the card before session start.

    This is the exact former race: without the shared row lock, both eligibility
    checks pass and maintenance can change lifecycle, question, or authority under
    a live answer. With the lock, the starter waits; it then either sees the
    committed archive or starts against the fully committed grounding update.
    """
    card = _grounded_card()
    db.add(card)
    await db.commit()
    if maintenance_action == "replace":
        # Exercise the compatibility branch that creates and commits a missing
        # settings row. That commit must happen before, never underneath, the
        # card's maintenance/session lock.
        settings = (await db.exec(select(Settings))).one()
        await db.delete(settings)
        await db.commit()

    url, kwargs = engine_kwargs(TEST_DATABASE_URL)
    engine = create_async_engine(url, **kwargs)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    maintenance_checked = asyncio.Event()
    release_maintenance = asyncio.Event()
    original_check = cards_router._require_no_live_session

    async def paused_after_live_check(request_db, checked_card):
        await original_check(request_db, checked_card)
        maintenance_checked.set()
        await release_maintenance.wait()

    monkeypatch.setattr(cards_router, "_require_no_live_session", paused_after_live_check)

    async def run_maintenance():
        token = auth._current_user_id.set(FOUNDER_USER_ID)
        try:
            async with factory() as request_db:
                if maintenance_action == "archive":
                    return await cards_router.archive_card(card.id, request_db)
                if maintenance_action == "replace":
                    return await cards_router.replace_card(
                        card.id,
                        ReplaceCard(
                            canonical_question=(
                                "A leader receives a write. When can it acknowledge?"
                            ),
                            schedule="now",
                        ),
                        request_db,
                    )
                return await cards_router.update_card_grounding(
                    card.id,
                    CardGroundingUpdate(answer_basis="A changed authority."),
                    request_db,
                )
        finally:
            auth._current_user_id.reset(token)

    async def run_start():
        token = auth._current_user_id.set(FOUNDER_USER_ID)
        try:
            async with factory() as request_db:
                return await sessions_router.start_session(card.id, False, request_db)
        finally:
            auth._current_user_id.reset(token)

    maintenance_task = asyncio.create_task(run_maintenance())
    start_task = None
    try:
        await asyncio.wait_for(maintenance_checked.wait(), timeout=3)
        start_task = asyncio.create_task(run_start())
        await asyncio.sleep(0.1)
        assert not start_task.done()
    finally:
        release_maintenance.set()

    try:
        await asyncio.wait_for(maintenance_task, timeout=3)
        if maintenance_action == "grounding":
            started = await asyncio.wait_for(start_task, timeout=3)
            assert started.turn_index == 0
        else:
            with pytest.raises(HTTPException) as exc_info:
                await asyncio.wait_for(start_task, timeout=3)
            assert exc_info.value.status_code == 409
            assert exc_info.value.detail == "card is archived"

        async with factory() as verify_db:
            stored = await verify_db.get(Card, card.id)
            assert stored is not None
            assert stored.lifecycle_status == (
                CARD_ACTIVE if maintenance_action == "grounding" else CARD_ARCHIVED
            )
            if maintenance_action == "grounding":
                assert stored.answer_basis == "A changed authority."
            sessions = (
                await verify_db.exec(select(Session).where(Session.card_id == card.id))
            ).all()
            assert len(sessions) == (1 if maintenance_action == "grounding" else 0)
            cards = (await verify_db.exec(select(Card))).all()
            assert len(cards) == (2 if maintenance_action == "replace" else 1)
    finally:
        await engine.dispose()


@pytest.mark.skipif(not TEST_ON_POSTGRES, reason="row-lock concurrency requires Postgres")
async def test_question_generation_discards_stale_grounding_inputs(db, monkeypatch):
    card = _grounded_card()
    card.canonical_question = None
    card.answer_basis = "Old authority."
    db.add(card)
    await db.commit()
    await db.rollback()
    url, kwargs = engine_kwargs(TEST_DATABASE_URL)
    engine = create_async_engine(url, **kwargs)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    generation_started = asyncio.Event()
    release_generation = asyncio.Event()
    seen_bases: list[str] = []

    async def generated_question(**kwargs):
        basis = kwargs["answer_basis"]
        seen_bases.append(basis)
        if len(seen_bases) == 1:
            generation_started.set()
            await release_generation.wait()
        return f"Question grounded in: {basis}"

    async def run_start():
        token = auth._current_user_id.set(FOUNDER_USER_ID)
        try:
            async with factory() as request_db:
                return await sessions_router.start_session(card.id, False, request_db)
        finally:
            auth._current_user_id.reset(token)

    async def run_grounding_update():
        token = auth._current_user_id.set(FOUNDER_USER_ID)
        try:
            async with factory() as request_db:
                return await cards_router.update_card_grounding(
                    card.id,
                    CardGroundingUpdate(answer_basis="New authority."),
                    request_db,
                )
        finally:
            auth._current_user_id.reset(token)

    monkeypatch.setattr(llm, "generate_question", generated_question)
    start_task = asyncio.create_task(run_start())
    try:
        await asyncio.wait_for(generation_started.wait(), timeout=3)
        await asyncio.wait_for(run_grounding_update(), timeout=3)
    finally:
        release_generation.set()

    started = await asyncio.wait_for(start_task, timeout=3)
    assert seen_bases == ["Old authority.", "New authority."]
    assert started.question == "Question grounded in: New authority."
    async with factory() as verify_db:
        stored = await verify_db.get(Card, card.id)
        assert stored is not None
        assert stored.answer_basis == "New authority."
        assert stored.canonical_question == started.question
        sessions = (
            await verify_db.exec(select(Session).where(Session.card_id == card.id))
        ).all()
        assert [session.question_asked for session in sessions] == [started.question]
    await engine.dispose()


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


async def test_postgres_grounding_and_session_start_share_the_card_lock(
    db, monkeypatch, postgres_session_factory
):
    card = _grounded_card()
    db.add(card)
    await db.commit()
    await db.rollback()
    grounding_has_lock = asyncio.Event()
    release_grounding = asyncio.Event()
    original_owned_card = cards_router.owned_card

    async def paused_owned_card(request_db, card_id, *, for_update=False):
        value = await original_owned_card(request_db, card_id, for_update=for_update)
        if for_update:
            grounding_has_lock.set()
            await release_grounding.wait()
        return value

    monkeypatch.setattr(cards_router, "owned_card", paused_owned_card)

    async def ground():
        async with postgres_session_factory() as request_db:
            return await _as_founder(
                cards_router.update_card_grounding(
                    card.id,
                    CardGroundingUpdate(
                        canonical_question="What makes a replicated entry committed?"
                    ),
                    request_db,
                )
            )

    async def start():
        async with postgres_session_factory() as request_db:
            return await _as_founder(
                sessions_router.start_session(card.id, False, request_db)
            )

    grounding_task = asyncio.create_task(ground())
    start_task = None
    try:
        await asyncio.wait_for(grounding_has_lock.wait(), timeout=3)
        start_task = asyncio.create_task(start())
        await asyncio.sleep(0.1)
        assert not start_task.done()
    finally:
        release_grounding.set()

    await asyncio.wait_for(grounding_task, timeout=3)
    started = await asyncio.wait_for(start_task, timeout=3)
    assert started.question == "What makes a replicated entry committed?"
    async with postgres_session_factory() as verify_db:
        stored = await verify_db.get(Card, card.id)
        session = (
            await verify_db.exec(select(Session).where(Session.card_id == card.id))
        ).one()
        assert stored is not None
        assert session.question_asked == stored.canonical_question


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


async def test_postgres_concurrent_replacement_creates_one_successor(
    db, postgres_session_factory
):
    card = _grounded_card()
    db.add(card)
    await db.commit()
    await db.rollback()

    async def replace(question: str):
        async with postgres_session_factory() as request_db:
            return await _as_founder(
                cards_router.replace_card(
                    card.id,
                    ReplaceCard(canonical_question=question, schedule="now"),
                    request_db,
                )
            )

    results = await asyncio.gather(
        replace("When may a leader acknowledge a committed write?"),
        replace("What proves a replicated entry is committed?"),
        return_exceptions=True,
    )
    successes = [result for result in results if not isinstance(result, Exception)]
    conflicts = [result for result in results if isinstance(result, HTTPException)]
    assert len(successes) == len(conflicts) == 1
    assert conflicts[0].status_code == 409
    async with postgres_session_factory() as verify_db:
        predecessor = await verify_db.get(Card, card.id)
        successors = (
            await verify_db.exec(select(Card).where(Card.replaces_card_id == card.id))
        ).all()
        assert predecessor is not None
        assert predecessor.lifecycle_status == CARD_ARCHIVED
        assert len(successors) == 1
        assert predecessor.replaced_by_card_id == successors[0].id


async def test_database_rejects_a_forked_replacement_lineage(db):
    predecessor = _grounded_card()
    db.add(predecessor)
    await db.flush()
    db.add(_grounded_card(topic="successor one", replaces_card_id=predecessor.id))
    db.add(_grounded_card(topic="successor two", replaces_card_id=predecessor.id))
    with pytest.raises(IntegrityError):
        await db.commit()
    await db.rollback()


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


async def test_replacement_cannot_restore_over_an_active_predecessor(client, db):
    old = _grounded_card()
    db.add(old)
    await db.commit()
    replaced = await client.post(
        f"/cards/{old.id}/replace",
        headers=API_HEADERS,
        json={
            "canonical_question": "When may a leader acknowledge a write?",
            "schedule": "now",
        },
    )
    replacement_id = uuid.UUID(replaced.json()["id"])
    assert (
        await client.post(
            f"/cards/{replacement_id}/archive", headers=API_HEADERS
        )
    ).status_code == 200
    assert (
        await client.post(f"/cards/{old.id}/restore", headers=API_HEADERS)
    ).status_code == 200

    response = await client.post(
        f"/cards/{replacement_id}/restore", headers=API_HEADERS
    )

    assert response.status_code == 409
    assert response.json()["detail"] == {
        "code": "replacement_is_active",
        "card_id": str(old.id),
    }
    cards = (await db.exec(select(Card))).all()
    assert [card.id for card in cards if card.lifecycle_status == CARD_ACTIVE] == [
        old.id
    ]


async def test_restore_checks_the_full_replacement_lineage(client, db):
    first, second, third, fourth = await _replacement_chain(
        db,
        CARD_ARCHIVED,
        CARD_ARCHIVED,
        CARD_ARCHIVED,
        CARD_ARCHIVED,
    )
    assert (
        await client.post(f"/cards/{second.id}/restore", headers=API_HEADERS)
    ).status_code == 200

    response = await client.post(f"/cards/{fourth.id}/restore", headers=API_HEADERS)

    assert response.status_code == 409
    assert response.json()["detail"] == {
        "code": "replacement_is_active",
        "card_id": str(second.id),
    }
    for card in (first, second, third, fourth):
        await db.refresh(card)
    assert [
        card.id
        for card in (first, second, third, fourth)
        if card.lifecycle_status == CARD_ACTIVE
    ] == [second.id]


async def test_restored_historical_card_cannot_fork_its_lineage(client, db):
    first, second = await _replacement_chain(db, CARD_ACTIVE, CARD_ARCHIVED)

    response = await client.post(
        f"/cards/{first.id}/replace",
        headers=API_HEADERS,
        json={
            "canonical_question": "A newer question",
            "schedule": "now",
        },
    )

    assert response.status_code == 409
    assert response.json()["detail"] == {
        "code": "newer_replacement_exists",
        "card_id": str(second.id),
    }
    await db.refresh(first)
    await db.refresh(second)
    assert first.replaced_by_card_id == second.id
    assert second.replaces_card_id == first.id
    assert len((await db.exec(select(Card))).all()) == 2


@pytest.mark.skipif(not TEST_ON_POSTGRES, reason="row-lock concurrency requires Postgres")
async def test_concurrent_restores_choose_one_active_lineage_member(db, monkeypatch):
    first, second, third = await _replacement_chain(
        db, CARD_ARCHIVED, CARD_ARCHIVED, CARD_ARCHIVED
    )
    await db.rollback()
    url, kwargs = engine_kwargs(TEST_DATABASE_URL)
    engine = create_async_engine(url, **kwargs)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    root_locked = asyncio.Event()
    release_root = asyncio.Event()
    original_owned_card = cards_router.owned_card
    paused = False

    async def pause_first_root_lock(request_db, card_id, *, for_update=False):
        nonlocal paused
        card = await original_owned_card(request_db, card_id, for_update=for_update)
        if for_update and card_id == first.id and not paused:
            paused = True
            root_locked.set()
            await release_root.wait()
        return card

    async def run_restore(card_id):
        token = auth._current_user_id.set(FOUNDER_USER_ID)
        try:
            async with factory() as request_db:
                return await cards_router.restore_card(card_id, request_db)
        finally:
            auth._current_user_id.reset(token)

    monkeypatch.setattr(cards_router, "owned_card", pause_first_root_lock)
    first_restore = asyncio.create_task(run_restore(second.id))
    second_restore = None
    try:
        await asyncio.wait_for(root_locked.wait(), timeout=3)
        second_restore = asyncio.create_task(run_restore(third.id))
        await asyncio.sleep(0.1)
        assert not second_restore.done()
    finally:
        release_root.set()

    await asyncio.wait_for(first_restore, timeout=3)
    with pytest.raises(HTTPException) as exc_info:
        await asyncio.wait_for(second_restore, timeout=3)
    assert exc_info.value.status_code == 409
    async with factory() as verify_db:
        lineage = (
            await verify_db.exec(
                select(Card).where(Card.id.in_([first.id, second.id, third.id]))
            )
        ).all()
        active = [card.id for card in lineage if card.lifecycle_status == CARD_ACTIVE]
        assert active == [second.id]
    await engine.dispose()


@pytest.mark.skipif(not TEST_ON_POSTGRES, reason="row-lock concurrency requires Postgres")
async def test_restore_waits_for_replacement_and_sees_its_new_successor(db, monkeypatch):
    first, second = await _replacement_chain(db, CARD_ARCHIVED, CARD_ACTIVE)
    await db.rollback()
    url, kwargs = engine_kwargs(TEST_DATABASE_URL)
    engine = create_async_engine(url, **kwargs)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    root_locked = asyncio.Event()
    release_root = asyncio.Event()
    original_owned_card = cards_router.owned_card
    paused = False

    async def pause_first_root_lock(request_db, card_id, *, for_update=False):
        nonlocal paused
        card = await original_owned_card(request_db, card_id, for_update=for_update)
        if for_update and card_id == first.id and not paused:
            paused = True
            root_locked.set()
            await release_root.wait()
        return card

    async def run_replace():
        token = auth._current_user_id.set(FOUNDER_USER_ID)
        try:
            async with factory() as request_db:
                return await cards_router.replace_card(
                    second.id,
                    ReplaceCard(
                        canonical_question="What makes this version current?",
                        schedule="now",
                    ),
                    request_db,
                )
        finally:
            auth._current_user_id.reset(token)

    async def run_restore():
        token = auth._current_user_id.set(FOUNDER_USER_ID)
        try:
            async with factory() as request_db:
                return await cards_router.restore_card(first.id, request_db)
        finally:
            auth._current_user_id.reset(token)

    monkeypatch.setattr(cards_router, "owned_card", pause_first_root_lock)
    replacement_task = asyncio.create_task(run_replace())
    restore_task = None
    try:
        await asyncio.wait_for(root_locked.wait(), timeout=3)
        restore_task = asyncio.create_task(run_restore())
        await asyncio.sleep(0.1)
        assert not restore_task.done()
    finally:
        release_root.set()

    replacement = await asyncio.wait_for(replacement_task, timeout=3)
    with pytest.raises(HTTPException) as exc_info:
        await asyncio.wait_for(restore_task, timeout=3)
    assert exc_info.value.status_code == 409
    async with factory() as verify_db:
        lineage = (await verify_db.exec(select(Card))).all()
        active = [card for card in lineage if card.lifecycle_status == CARD_ACTIVE]
        assert [card.id for card in active] == [replacement.id]
        stored_second = await verify_db.get(Card, second.id)
        stored_replacement = await verify_db.get(Card, replacement.id)
        assert stored_second is not None
        assert stored_replacement is not None
        assert stored_second.replaced_by_card_id == replacement.id
        assert stored_replacement.replaces_card_id == second.id
    await engine.dispose()


async def test_postgres_concurrent_related_restores_activate_only_one_side(
    db, postgres_session_factory
):
    predecessor = _grounded_card(lifecycle_status=CARD_ARCHIVED)
    db.add(predecessor)
    await db.flush()
    replacement = _grounded_card(
        topic="replacement",
        lifecycle_status=CARD_ARCHIVED,
        replaces_card_id=predecessor.id,
    )
    db.add(replacement)
    await db.flush()
    predecessor.replaced_by_card_id = replacement.id
    db.add(predecessor)
    await db.commit()
    await db.rollback()

    async def restore(card_id: uuid.UUID):
        async with postgres_session_factory() as request_db:
            return await _as_founder(cards_router.restore_card(card_id, request_db))

    results = await asyncio.gather(
        restore(predecessor.id), restore(replacement.id), return_exceptions=True
    )
    assert len([result for result in results if not isinstance(result, Exception)]) == 1
    conflicts = [result for result in results if isinstance(result, HTTPException)]
    assert len(conflicts) == 1
    assert conflicts[0].status_code == 409
    async with postgres_session_factory() as verify_db:
        cards = (
            await verify_db.exec(
                select(Card).where(col(Card.id).in_((predecessor.id, replacement.id)))
            )
        ).all()
        assert [card.lifecycle_status for card in cards].count(CARD_ACTIVE) == 1
