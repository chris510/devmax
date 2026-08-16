import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app import auth
from app.config import get_settings
from app.db import engine_kwargs
from app.models import (
    CARD_ARCHIVED,
    FOUNDER_USER_ID,
    STATUS_AWAITING_FOLLOW_UP,
    STATUS_OPEN,
    MaterialSource,
    PendingCapture,
    Session,
    SessionProbe,
    Settings,
    User,
)
from app.routers import cards as cards_router
from app.routers import internal
from app.routers import sessions as sessions_router
from app.schemas import DraftUpdate, ScoredAnswerIn
from app.services import llm, usage
from app.services.llm import ScoreResult
from app.services.push import PushDelivery
from tests.conftest import (
    API_HEADERS,
    CRON_HEADERS,
    TEST_DATABASE_URL,
    TEST_ON_POSTGRES,
    local_today,
    local_today_at,
    make_card,
)

LA = ZoneInfo("America/Los_Angeles")


@pytest.fixture
async def concurrent_session_factory():
    """Independent connections for lock-order tests; SQLite ignores FOR UPDATE."""
    if not TEST_ON_POSTGRES:
        pytest.skip("row-lock concurrency requires Postgres")
    url, kwargs = engine_kwargs(TEST_DATABASE_URL)
    engine = create_async_engine(url, **kwargs)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        yield factory
    finally:
        await engine.dispose()


async def open_learning_as_founder(factory, card_id):
    token = auth._current_user_id.set(FOUNDER_USER_ID)
    try:
        async with factory() as learning_db:
            return await cards_router.open_learning(card_id, learning_db)
    finally:
        auth._current_user_id.reset(token)


async def start_session_as_founder(factory, card_id):
    token = auth._current_user_id.set(FOUNDER_USER_ID)
    try:
        async with factory() as session_db:
            return await sessions_router.start_session(card_id, False, session_db)
    finally:
        auth._current_user_id.reset(token)


async def submit_answer_as_founder(factory, session_id, *, text, turn_index):
    token = auth._current_user_id.set(FOUNDER_USER_ID)
    try:
        async with factory() as answer_db:
            return await sessions_router.submit_answer(
                session_id,
                ScoredAnswerIn(text=text, turn_index=turn_index),
                answer_db,
            )
    finally:
        auth._current_user_id.reset(token)


async def save_draft_as_founder(factory, session_id, *, text, turn_index):
    token = auth._current_user_id.set(FOUNDER_USER_ID)
    try:
        async with factory() as draft_db:
            return await sessions_router.save_draft(
                session_id,
                DraftUpdate(draft_text=text, turn_index=turn_index),
                draft_db,
            )
    finally:
        auth._current_user_id.reset(token)

V1_RUBRIC = {
    "mechanism": "Move only keys in the new node's ranges.",
    "acceptable_alternative": "Equivalent ring or rendezvous terminology is valid.",
    "trade_off": "Virtual nodes improve balance at a metadata cost.",
    "failure_mode": "A hot key stays hot even when ownership is balanced.",
    "misconception": "Adding a node does not remap every key.",
}

V2_RUBRIC = {
    "essential_account": V1_RUBRIC["mechanism"],
    "acceptable_alternative": V1_RUBRIC["acceptable_alternative"],
    "depth_extension": V1_RUBRIC["trade_off"],
    "boundary_extension": V1_RUBRIC["failure_mode"],
    "misconception": V1_RUBRIC["misconception"],
}


def grounded_card(**overrides):
    defaults = {
        "canonical_question": "What moves when a node joins the ring?",
        "source_url": "https://example.com/consistent-hashing",
        "source_section": "Virtual nodes",
        "source_label": "Reviewed systems notes",
        "answer_basis": "Hash both nodes and keys onto a ring.",
        "answer_rubric": V1_RUBRIC,
    }
    return make_card(**{**defaults, **overrides})


@pytest.mark.parametrize("rubric", [V1_RUBRIC, V2_RUBRIC])
async def test_learning_returns_one_flat_normalized_contract(client, db, rubric):
    card = grounded_card(answer_rubric=rubric, source_excerpt="The trusted excerpt.")
    db.add(card)
    await db.commit()

    response = await client.post(f"/cards/{card.id}/learning", headers=API_HEADERS)

    assert response.status_code == 200
    body = response.json()
    assert set(body) == {
        "card_id",
        "topic",
        "category",
        "source_url",
        "source_section",
        "source_label",
        "source_excerpt",
        "core_explanation",
        "essential_account",
        "acceptable_alternative",
        "depth_extension",
        "boundary_extension",
        "misconception",
        "recall_available_at",
    }
    assert body["card_id"] == str(card.id)
    assert body["core_explanation"] == "Hash both nodes and keys onto a ring."
    assert body["essential_account"] == V2_RUBRIC["essential_account"]
    assert body["acceptable_alternative"] == V2_RUBRIC["acceptable_alternative"]
    assert body["depth_extension"] == V2_RUBRIC["depth_extension"]
    assert body["boundary_extension"] == V2_RUBRIC["boundary_extension"]
    assert body["misconception"] == V2_RUBRIC["misconception"]
    assert body["source_excerpt"] == "The trusted excerpt."
    assert "canonical_question" not in body


async def test_learning_falls_back_to_answer_anchor_and_linked_source_title(client, db):
    source = MaterialSource(
        user_id=FOUNDER_USER_ID,
        title="Imported architecture guide",
        source_text="A complete trusted source.",
    )
    db.add(source)
    await db.flush()
    card = grounded_card(
        source_id=source.id,
        source_url="",
        source_section="",
        source_label="",
        source_excerpt="A focused trusted excerpt.",
        # A truthy but empty-after-trim basis must not mask the confirmed anchor.
        answer_basis=" \n ",
        answer_anchor="The imported source explains the ownership ring.",
        answer_rubric={},
    )
    db.add(card)
    await db.commit()

    detail = await client.get(f"/cards/{card.id}", headers=API_HEADERS)
    body = (
        await client.post(f"/cards/{card.id}/learning", headers=API_HEADERS)
    ).json()

    assert detail.json()["learning_available"] is True
    assert detail.json()["source_label"] == "Imported architecture guide"
    assert body["core_explanation"] == (
        "The imported source explains the ownership ring."
    )
    assert body["source_label"] == "Imported architecture guide"
    assert body["source_excerpt"] == "A focused trusted excerpt."
    assert body["essential_account"] == ""
    assert body["depth_extension"] == ""


async def test_v2_storage_keeps_legacy_capture_and_maintenance_wires(
    client, db, monkeypatch
):
    """A V2 save must reopen in the current iOS editors with every field filled."""
    monkeypatch.setattr(get_settings(), "scoring_contract_version", 2)
    card = make_card()
    db.add(card)
    await db.commit()
    grounding = {
        "source_url": "https://example.com/authority",
        "source_section": "Relevant section",
        "source_label": "Reviewed source",
        "answer_basis": "The trusted core explanation.",
        "answer_rubric": V1_RUBRIC,
        "canonical_question": "Explain the mechanism in this scenario.",
    }

    card_response = await client.patch(
        f"/cards/{card.id}/grounding", headers=API_HEADERS, json=grounding
    )
    created_capture = await client.post(
        "/captures",
        headers=API_HEADERS,
        json={"topic": "New grounded gap", "context": "Interview debrief"},
    )
    capture_response = await client.patch(
        f"/captures/{created_capture.json()['id']}",
        headers=API_HEADERS,
        json=grounding,
    )

    assert card_response.json()["answer_rubric"] == V1_RUBRIC
    assert capture_response.json()["answer_rubric"] == V1_RUBRIC
    reopened = await client.get(f"/cards/{card.id}/maintenance", headers=API_HEADERS)
    assert reopened.json()["answer_rubric"] == V1_RUBRIC

    await db.refresh(card)
    capture = await db.get(PendingCapture, uuid.UUID(created_capture.json()["id"]))
    assert card.answer_rubric == V2_RUBRIC
    assert capture.answer_rubric == V2_RUBRIC


async def test_learning_changes_only_exposure_metadata(client, db):
    reviewed_at = datetime.now(UTC) - timedelta(days=3)
    pushed_at = datetime.now(UTC) - timedelta(hours=1)
    card = grounded_card(
        ease_factor=2.31,
        interval_days=17,
        repetitions=4,
        next_review_at=local_today() + timedelta(days=17),
        last_score=4,
        last_accuracy=4,
        last_depth=3,
        last_boundaries=2,
        last_reviewed_at=reviewed_at,
        mastery_summary="Strong mechanism; repair hot-key boundaries.",
        missed_count=2,
        last_pushed_at=pushed_at,
    )
    db.add(card)
    await db.commit()
    await db.refresh(card)
    invariant = (
        card.ease_factor,
        card.interval_days,
        card.repetitions,
        card.next_review_at,
        card.last_score,
        card.last_accuracy,
        card.last_depth,
        card.last_boundaries,
        card.last_reviewed_at,
        card.mastery_summary,
        card.missed_count,
        card.last_pushed_at,
    )

    response = await client.post(f"/cards/{card.id}/learning", headers=API_HEADERS)

    assert response.status_code == 200
    await db.refresh(card)
    assert (
        card.ease_factor,
        card.interval_days,
        card.repetitions,
        card.next_review_at,
        card.last_score,
        card.last_accuracy,
        card.last_depth,
        card.last_boundaries,
        card.last_reviewed_at,
        card.mastery_summary,
        card.missed_count,
        card.last_pushed_at,
    ) == invariant
    assert card.last_learning_exposure_at is not None
    assert card.recall_not_before_at is not None
    assert not (
        await db.exec(select(Session).where(Session.card_id == card.id))
    ).all()


async def test_each_successful_reexposure_monotonically_extends_the_gate(
    client, db, monkeypatch
):
    card = grounded_card()
    db.add(card)
    await db.commit()
    times = iter(
        [
            datetime(2026, 8, 11, 23, 0, tzinfo=LA),
            datetime(2026, 8, 11, 23, 30, tzinfo=LA),
        ]
    )
    monkeypatch.setattr(cards_router, "now_in", lambda _tz: next(times))

    first = (
        await client.post(f"/cards/{card.id}/learning", headers=API_HEADERS)
    ).json()
    second = (
        await client.post(f"/cards/{card.id}/learning", headers=API_HEADERS)
    ).json()

    first_gate = datetime.fromisoformat(first["recall_available_at"])
    second_gate = datetime.fromisoformat(second["recall_available_at"])
    assert second_gate == first_gate + timedelta(minutes=30)
    await db.refresh(card)
    assert card.last_learning_exposure_at.replace(tzinfo=UTC) == datetime(
        2026, 8, 12, 6, 30, tzinfo=UTC
    )


async def test_reexposure_never_shortens_a_preexisting_later_gate(
    client, db, monkeypatch
):
    later_gate = datetime(2026, 8, 20, 12, 0, tzinfo=UTC)
    card = grounded_card(recall_not_before_at=later_gate)
    db.add(card)
    await db.commit()
    monkeypatch.setattr(
        cards_router,
        "now_in",
        lambda _tz: datetime(2026, 8, 11, 12, 0, tzinfo=LA),
    )

    response = await client.post(f"/cards/{card.id}/learning", headers=API_HEADERS)

    assert datetime.fromisoformat(response.json()["recall_available_at"]) == later_gate
    await db.refresh(card)
    stored_gate = card.recall_not_before_at.replace(tzinfo=UTC)
    assert stored_gate == later_gate


@pytest.mark.parametrize(
    "overrides",
    [
        {"answer_basis": "", "answer_anchor": ""},
        {"source_url": "", "source_section": "", "source_label": ""},
        {
            "answer_rubric": {
                **V1_RUBRIC,
                "failure_mode": "",
            }
        },
    ],
)
async def test_learning_fails_closed_when_authority_is_incomplete(
    client, db, overrides
):
    card = grounded_card(**overrides)
    db.add(card)
    await db.commit()

    detail = await client.get(f"/cards/{card.id}", headers=API_HEADERS)
    response = await client.post(f"/cards/{card.id}/learning", headers=API_HEADERS)

    assert detail.json()["learning_available"] is False
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "learning_material_unavailable"
    await db.refresh(card)
    assert card.last_learning_exposure_at is None
    assert card.recall_not_before_at is None


async def test_learning_requires_an_owned_active_card(client, db):
    archived = grounded_card(lifecycle_status=CARD_ARCHIVED)
    other_user = User(id=uuid.uuid4())
    db.add(other_user)
    await db.flush()
    foreign = grounded_card(user_id=other_user.id)
    db.add(archived)
    db.add(foreign)
    await db.commit()

    archived_response = await client.post(
        f"/cards/{archived.id}/learning", headers=API_HEADERS
    )
    foreign_response = await client.post(
        f"/cards/{foreign.id}/learning", headers=API_HEADERS
    )

    assert archived_response.status_code == 409
    assert archived_response.json()["detail"]["code"] == "card_archived"
    assert foreign_response.status_code == 404
    archived_detail = await client.get(f"/cards/{archived.id}", headers=API_HEADERS)
    assert archived_detail.json()["learning_available"] is False
    await db.refresh(archived)
    await db.refresh(foreign)
    assert archived.last_learning_exposure_at is None
    assert foreign.last_learning_exposure_at is None


async def test_learning_refuses_to_reveal_authority_during_a_live_session(client, db):
    card = grounded_card()
    db.add(card)
    await db.flush()
    db.add(
        Session(
            card_id=card.id,
            question_asked=card.canonical_question,
            status=STATUS_OPEN,
        )
    )
    await db.commit()

    detail = await client.get(f"/cards/{card.id}", headers=API_HEADERS)
    response = await client.post(f"/cards/{card.id}/learning", headers=API_HEADERS)

    assert detail.json()["learning_available"] is False
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "live_session"
    await db.refresh(card)
    assert card.last_learning_exposure_at is None
    assert card.recall_not_before_at is None


async def test_gated_card_is_hidden_from_due_until_the_instant_expires(client, db):
    card = grounded_card(
        next_review_at=local_today() - timedelta(days=3),
        recall_not_before_at=datetime.now(UTC) + timedelta(hours=8),
    )
    db.add(card)
    await db.commit()

    gated = await client.get("/cards/due", headers=API_HEADERS)
    summary = (await client.get("/cards", headers=API_HEADERS)).json()[0]
    detail = (await client.get(f"/cards/{card.id}", headers=API_HEADERS)).json()

    assert gated.json() == []
    assert summary["recall_not_before_at"] is not None
    assert "overdue" not in summary["due_label"]
    assert detail["learning_available"] is True
    assert detail["source_label"] == "Reviewed systems notes"
    assert detail["source_section"] == "Virtual nodes"

    card.recall_not_before_at = datetime.now(UTC) - timedelta(microseconds=1)
    db.add(card)
    await db.commit()
    available = await client.get("/cards/due", headers=API_HEADERS)
    assert [row["id"] for row in available.json()] == [str(card.id)]
    assert available.json()[0]["due_label"] == "3 days overdue"


@pytest.mark.parametrize("query", ["", "?practice=true"])
async def test_session_start_enforces_gate_before_usage_or_model_call(
    client, db, monkeypatch, query
):
    calls = []

    async def forbidden(*_args, **_kwargs):
        calls.append(True)
        raise AssertionError("the gate must run before cost or question generation")

    monkeypatch.setattr(usage, "ensure_available", forbidden)
    monkeypatch.setattr(llm, "generate_question", forbidden)
    gate = datetime.now(UTC) + timedelta(hours=8)
    card = grounded_card(canonical_question=None, recall_not_before_at=gate)
    db.add(card)
    await db.commit()

    response = await client.post(
        f"/cards/{card.id}/sessions{query}", headers=API_HEADERS
    )

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "recall_cooldown"
    assert datetime.fromisoformat(
        response.json()["detail"]["recall_available_at"]
    ) == gate
    assert calls == []
    assert not (
        await db.exec(select(Session).where(Session.card_id == card.id))
    ).all()


async def test_trigger_review_excludes_learning_gated_cards(
    client, db, in_window, monkeypatch
):
    sent = []

    async def capture_push(**kwargs):
        sent.append(kwargs)
        return PushDelivery(sent=1, attempted=1)

    monkeypatch.setattr(internal, "send_push", capture_push)
    db.add(
        grounded_card(
            next_review_at=local_today() - timedelta(days=3),
            recall_not_before_at=datetime.now(UTC) + timedelta(hours=8),
        )
    )
    await db.commit()

    response = await client.post("/internal/trigger-review", headers=CRON_HEADERS)

    assert response.json()["reason"] == "nothing_due"
    assert sent == []


async def test_trigger_review_skips_a_live_card_and_offers_the_next_due_card(
    client, db, in_window, monkeypatch
):
    sent = []

    async def capture_push(**kwargs):
        sent.append(kwargs)
        return PushDelivery(sent=1, attempted=1)

    monkeypatch.setattr(internal, "send_push", capture_push)
    live_card = grounded_card(
        topic="Answer already in progress",
        next_review_at=local_today() - timedelta(days=3),
    )
    available_card = grounded_card(
        topic="Next honest offer",
        next_review_at=local_today() - timedelta(days=2),
    )
    db.add(live_card)
    db.add(available_card)
    await db.flush()
    db.add(
        Session(
            card_id=live_card.id,
            question_asked=live_card.canonical_question,
            status=STATUS_OPEN,
        )
    )
    await db.commit()

    response = await client.post("/internal/trigger-review", headers=CRON_HEADERS)

    assert response.json()["card_id"] == str(available_card.id)
    # Resumable work remains in the due count; it just is not pushed again.
    assert response.json()["due_count"] == 2
    assert sent[0]["body"] == "Next honest offer"


@pytest.mark.skipif(not TEST_ON_POSTGRES, reason="row-lock concurrency requires Postgres")
async def test_concurrent_first_opens_generate_one_canonical_question(
    db, monkeypatch, concurrent_session_factory
):
    card = grounded_card(canonical_question=None)
    db.add(card)
    await db.commit()

    provider_entered = asyncio.Event()
    release_provider = asyncio.Event()
    calls = 0

    async def paused_question(**_kwargs):
        nonlocal calls
        calls += 1
        provider_entered.set()
        await release_provider.wait()
        return "Which keys move when a node joins the ring?"

    monkeypatch.setattr(llm, "generate_question", paused_question)
    first = asyncio.create_task(
        start_session_as_founder(concurrent_session_factory, card.id)
    )
    second = None
    try:
        await asyncio.wait_for(provider_entered.wait(), timeout=3)
        second = asyncio.create_task(
            start_session_as_founder(concurrent_session_factory, card.id)
        )
        await asyncio.sleep(0.1)
        assert calls == 1
        assert not second.done()
    finally:
        release_provider.set()

    first_result = await asyncio.wait_for(first, timeout=3)
    second_result = await asyncio.wait_for(second, timeout=3)
    assert calls == 1
    assert second_result.session_id == first_result.session_id
    assert second_result.question == first_result.question
    assert second_result.resumed is True

    async with concurrent_session_factory() as verify_db:
        stored = await verify_db.get(type(card), card.id)
        sessions = (
            await verify_db.exec(select(Session).where(Session.card_id == card.id))
        ).all()
        assert stored.canonical_question == first_result.question
        assert len(sessions) == 1


@pytest.mark.skipif(not TEST_ON_POSTGRES, reason="row-lock concurrency requires Postgres")
@pytest.mark.parametrize("same_payload", [True, False])
async def test_concurrent_same_turn_submissions_reconcile_by_index_and_text(
    db, monkeypatch, concurrent_session_factory, same_payload
):
    """One turn scores once; duplicate payload replays and key reuse conflicts."""
    card = grounded_card()
    session = Session(
        card_id=card.id,
        question_asked=card.canonical_question or "",
        status=STATUS_OPEN,
    )
    db.add(card)
    db.add(session)
    await db.commit()

    provider_entered = asyncio.Event()
    release_provider = asyncio.Event()
    calls = 0

    async def paused_score(**_kwargs):
        nonlocal calls
        calls += 1
        provider_entered.set()
        await release_provider.wait()
        return ScoreResult(
            status="follow_up",
            follow_up_question="One more — name the ownership boundary?",
        )

    monkeypatch.setattr(llm, "score_answer", paused_score)
    first = asyncio.create_task(
        submit_answer_as_founder(
            concurrent_session_factory,
            session.id,
            text="the committed answer",
            turn_index=0,
        )
    )
    second = None
    try:
        await asyncio.wait_for(provider_entered.wait(), timeout=3)
        second = asyncio.create_task(
            submit_answer_as_founder(
                concurrent_session_factory,
                session.id,
                text=("the committed answer" if same_payload else "different text"),
                turn_index=0,
            )
        )
        await asyncio.sleep(0.1)
        assert calls == 1
        assert not second.done()
    finally:
        release_provider.set()

    first_result = await asyncio.wait_for(first, timeout=3)
    assert first_result.turn_index == 1
    if same_payload:
        second_result = await asyncio.wait_for(second, timeout=3)
        assert second_result == first_result
    else:
        with pytest.raises(HTTPException) as exc_info:
            await asyncio.wait_for(second, timeout=3)
        assert exc_info.value.status_code == 409
        assert "different text" in exc_info.value.detail
    assert calls == 1

    async with concurrent_session_factory() as verify_db:
        stored = await verify_db.get(Session, session.id)
        probes = (
            await verify_db.exec(
                select(SessionProbe).where(SessionProbe.session_id == session.id)
            )
        ).all()
        assert stored.status == STATUS_AWAITING_FOLLOW_UP
        assert stored.answer_text == "the committed answer"
        assert len(probes) == 1


@pytest.mark.skipif(not TEST_ON_POSTGRES, reason="row-lock concurrency requires Postgres")
async def test_stale_draft_update_rechecks_turn_after_waiting_on_answer_commit(
    db, concurrent_session_factory
):
    """Postgres re-evaluates the conditional UPDATE after the session row unlocks."""
    card = grounded_card()
    session = Session(
        card_id=card.id,
        question_asked=card.canonical_question or "",
        status=STATUS_OPEN,
    )
    db.add(card)
    db.add(session)
    await db.commit()

    async with concurrent_session_factory() as advancing_db:
        advancing = await advancing_db.get(Session, session.id)
        advancing.status = STATUS_AWAITING_FOLLOW_UP
        advancing.draft_text = ""
        advancing_db.add(advancing)
        advancing_db.add(
            SessionProbe(
                session_id=session.id,
                idx=1,
                question="One more — name the ownership boundary?",
            )
        )
        await advancing_db.flush()

        stale = asyncio.create_task(
            save_draft_as_founder(
                concurrent_session_factory,
                session.id,
                text="late opening partial",
                turn_index=0,
            )
        )
        await asyncio.sleep(0.1)
        assert not stale.done()
        await advancing_db.commit()

    response = await asyncio.wait_for(stale, timeout=3)
    assert response.status_code == 204
    async with concurrent_session_factory() as verify_db:
        stored = await verify_db.get(Session, session.id)
        assert stored.status == STATUS_AWAITING_FOLLOW_UP
        assert stored.draft_text == ""


@pytest.mark.skipif(not TEST_ON_POSTGRES, reason="row-lock concurrency requires Postgres")
async def test_probe_to_probe_commit_orders_a_stale_draft_on_the_session_row(
    db, monkeypatch, concurrent_session_factory
):
    """Probe 1 -> 2 has a barrier even when no Session value becomes dirty."""
    card = grounded_card()
    session = Session(
        card_id=card.id,
        question_asked=card.canonical_question or "",
        answer_text="opening evidence",
        follow_up_used=True,
        draft_text="",
        status=STATUS_AWAITING_FOLLOW_UP,
    )
    db.add(card)
    db.add(session)
    await db.flush()
    db.add(
        SessionProbe(
            session_id=session.id,
            idx=1,
            question="First probe?",
        )
    )
    await db.commit()

    async def second_probe(**_kwargs):
        return ScoreResult(
            status="follow_up",
            follow_up_question="Second probe?",
        )

    original_owned_session = sessions_router._owned_session
    answer_barrier_held = asyncio.Event()
    release_answer = asyncio.Event()
    paused = False

    async def pause_first_write_barrier(
        request_db, session_id, *, for_update=False
    ):
        nonlocal paused
        stored = await original_owned_session(
            request_db, session_id, for_update=for_update
        )
        if for_update and not paused:
            paused = True
            answer_barrier_held.set()
            await release_answer.wait()
        return stored

    monkeypatch.setattr(llm, "score_answer", second_probe)
    monkeypatch.setattr(sessions_router, "_owned_session", pause_first_write_barrier)
    answer = asyncio.create_task(
        submit_answer_as_founder(
            concurrent_session_factory,
            session.id,
            text="probe one evidence",
            turn_index=1,
        )
    )
    stale_draft = None
    try:
        await asyncio.wait_for(answer_barrier_held.wait(), timeout=3)
        stale_draft = asyncio.create_task(
            save_draft_as_founder(
                concurrent_session_factory,
                session.id,
                text="late probe one partial",
                turn_index=1,
            )
        )
        await asyncio.sleep(0.1)
        assert not stale_draft.done()
    finally:
        release_answer.set()

    answer_result = await asyncio.wait_for(answer, timeout=3)
    assert answer_result.turn_index == 2
    draft_result = await asyncio.wait_for(stale_draft, timeout=3)
    assert draft_result.status_code == 204

    async with concurrent_session_factory() as verify_db:
        stored = await verify_db.get(Session, session.id)
        probes = (
            await verify_db.exec(
                select(SessionProbe)
                .where(SessionProbe.session_id == session.id)
                .order_by(SessionProbe.idx)
            )
        ).all()
        assert stored.status == STATUS_AWAITING_FOLLOW_UP
        assert stored.draft_text == ""
        assert [(probe.idx, probe.answer) for probe in probes] == [
            (1, "probe one evidence"),
            (2, ""),
        ]


@pytest.mark.skipif(not TEST_ON_POSTGRES, reason="row-lock concurrency requires Postgres")
async def test_draft_session_barrier_does_not_wait_for_provider_work(
    db, monkeypatch, concurrent_session_factory
):
    card = grounded_card()
    session = Session(
        card_id=card.id,
        question_asked=card.canonical_question or "",
        status=STATUS_OPEN,
    )
    db.add(card)
    db.add(session)
    await db.commit()

    provider_entered = asyncio.Event()
    release_provider = asyncio.Event()

    async def paused_score(**_kwargs):
        provider_entered.set()
        await release_provider.wait()
        return ScoreResult(
            status="follow_up",
            follow_up_question="First probe?",
        )

    monkeypatch.setattr(llm, "score_answer", paused_score)
    answer = asyncio.create_task(
        submit_answer_as_founder(
            concurrent_session_factory,
            session.id,
            text="opening evidence",
            turn_index=0,
        )
    )
    try:
        await asyncio.wait_for(provider_entered.wait(), timeout=3)
        draft = await asyncio.wait_for(
            save_draft_as_founder(
                concurrent_session_factory,
                session.id,
                text="durable while scoring",
                turn_index=0,
            ),
            timeout=3,
        )
        assert draft.status_code == 204
        async with concurrent_session_factory() as verify_db:
            stored = await verify_db.get(Session, session.id)
            assert stored.draft_text == "durable while scoring"
    finally:
        release_provider.set()

    result = await asyncio.wait_for(answer, timeout=3)
    assert result.turn_index == 1
    async with concurrent_session_factory() as verify_db:
        stored = await verify_db.get(Session, session.id)
        assert stored.draft_text == ""


@pytest.mark.skipif(not TEST_ON_POSTGRES, reason="row-lock concurrency requires Postgres")
async def test_push_holds_candidate_lock_until_stamp_before_learning_exposure(
    db, monkeypatch, concurrent_session_factory
):
    card = grounded_card(next_review_at=local_today() - timedelta(days=3))
    db.add(card)
    await db.commit()
    settings = (
        await db.exec(select(Settings).where(Settings.user_id == FOUNDER_USER_ID))
    ).one()

    push_started = asyncio.Event()
    release_push = asyncio.Event()
    trigger_now = local_today_at(7, 30)
    learning_now = trigger_now + timedelta(minutes=1)
    monkeypatch.setattr(internal, "now_in", lambda _tz: trigger_now)
    monkeypatch.setattr(cards_router, "now_in", lambda _tz: learning_now)

    async def paused_push(**_kwargs):
        push_started.set()
        await release_push.wait()
        return PushDelivery(sent=1, attempted=1)

    monkeypatch.setattr(internal, "send_push", paused_push)

    async def run_trigger():
        async with concurrent_session_factory() as trigger_db:
            return await internal._trigger_review_for_user(
                settings, FOUNDER_USER_ID, trigger_db
            )

    trigger_task = asyncio.create_task(run_trigger())
    learning_task = None
    try:
        await asyncio.wait_for(push_started.wait(), timeout=3)
        learning_task = asyncio.create_task(
            open_learning_as_founder(concurrent_session_factory, card.id)
        )
        await asyncio.sleep(0.1)
        learned_while_push_was_unstamped = learning_task.done()
    finally:
        release_push.set()

    trigger_result = await asyncio.wait_for(trigger_task, timeout=3)
    learning_result = await asyncio.wait_for(learning_task, timeout=3)

    assert learned_while_push_was_unstamped is False
    assert trigger_result.sent is True
    assert learning_result.card_id == card.id
    async with concurrent_session_factory() as verify_db:
        stored = await verify_db.get(type(card), card.id)
        assert stored.last_pushed_at is not None
        assert stored.last_learning_exposure_at >= stored.last_pushed_at


@pytest.mark.skipif(not TEST_ON_POSTGRES, reason="row-lock concurrency requires Postgres")
async def test_check_missed_reloads_after_concurrent_learning_engagement(
    db, monkeypatch, concurrent_session_factory
):
    card = grounded_card(last_pushed_at=datetime.now(UTC) - timedelta(hours=6))
    db.add(card)
    await db.commit()
    candidates_read = asyncio.Event()
    continue_check = asyncio.Event()

    async def run_check():
        async with concurrent_session_factory() as missed_db:
            original_exec = missed_db.exec
            first_query = True

            async def pause_after_candidates(*args, **kwargs):
                nonlocal first_query
                result = await original_exec(*args, **kwargs)
                if first_query:
                    first_query = False
                    candidates_read.set()
                    await continue_check.wait()
                return result

            monkeypatch.setattr(missed_db, "exec", pause_after_candidates)
            return await internal.check_missed(missed_db)

    check_task = asyncio.create_task(run_check())
    try:
        await asyncio.wait_for(candidates_read.wait(), timeout=3)
        await open_learning_as_founder(concurrent_session_factory, card.id)
    finally:
        continue_check.set()

    result = await asyncio.wait_for(check_task, timeout=3)

    assert result == {"marked_missed": 0}
    async with concurrent_session_factory() as verify_db:
        stored = await verify_db.get(type(card), card.id)
        assert stored.missed_count == 0
        assert stored.missed_counted_at is None
        assert stored.push_resolved_at == stored.last_pushed_at


async def test_learning_after_a_push_counts_as_engagement_not_a_miss(client, db):
    card = grounded_card(last_pushed_at=datetime.now(UTC) - timedelta(hours=6))
    db.add(card)
    await db.commit()

    learned = await client.post(f"/cards/{card.id}/learning", headers=API_HEADERS)
    missed = await client.post("/internal/check-missed", headers=CRON_HEADERS)

    assert learned.status_code == 200
    assert missed.json() == {"marked_missed": 0}
    await db.refresh(card)
    assert card.missed_count == 0
    assert card.missed_counted_at is None
    assert card.push_resolved_at == card.last_pushed_at
