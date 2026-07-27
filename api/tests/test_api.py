import uuid
from datetime import timedelta

import pytest

from app.models import STATUS_AWAITING_FOLLOW_UP, STATUS_COMPLETE, Session
from app.routers import internal
from app.services import llm
from app.services.llm import LLMError, ScoreResult
from tests.conftest import API_HEADERS, CRON_HEADERS, local_today, make_card


@pytest.fixture
def stub_llm(monkeypatch):
    """Mock Anthropic entirely — no live calls in CI."""

    async def _question(**_kwargs) -> str:
        return "You're adding a node to a consistent-hashing ring. What moves?"

    calls: list[dict] = []

    async def _score(**kwargs) -> ScoreResult:
        calls.append(kwargs)
        return _score.result

    _score.result = ScoreResult(status="complete", score=4, feedback="Good.", mastery_summary="ok")

    monkeypatch.setattr(llm, "generate_question", _question)
    monkeypatch.setattr(llm, "score_answer", _score)
    return _score, calls


# --- auth -------------------------------------------------------------------


async def test_client_endpoint_rejects_missing_key(client):
    assert (await client.get("/cards/due")).status_code == 401


async def test_client_endpoint_rejects_wrong_key(client):
    resp = await client.get("/cards/due", headers={"X-API-Key": "nope"})
    assert resp.status_code == 401


async def test_internal_endpoint_rejects_client_key(client):
    """The two secrets are independent — the client key must not open /internal."""
    resp = await client.post("/internal/check-missed", headers=API_HEADERS)
    assert resp.status_code == 401


async def test_internal_endpoint_accepts_cron_secret(client):
    resp = await client.post("/internal/check-missed", headers=CRON_HEADERS)
    assert resp.status_code == 200


# --- due queue --------------------------------------------------------------


async def test_due_excludes_desk_cards(client, db):
    today = local_today()
    db.add(make_card(topic="Consistent hashing", next_review_at=today))
    db.add(make_card(topic="Multi-source BFS", delivery_mode="desk", next_review_at=today))
    await db.commit()

    body = (await client.get("/cards/due", headers=API_HEADERS)).json()
    assert [c["topic"] for c in body] == ["Consistent hashing"]


async def test_due_orders_most_overdue_first(client, db):
    today = local_today()
    db.add(make_card(topic="Due today", next_review_at=today))
    db.add(make_card(topic="Three days over", next_review_at=today - timedelta(days=3)))
    await db.commit()

    body = (await client.get("/cards/due", headers=API_HEADERS)).json()
    assert [c["topic"] for c in body] == ["Three days over", "Due today"]
    assert body[0]["due_label"] == "3 days overdue"
    assert body[1]["due_label"] == "due today"


async def test_resumable_reflects_stored_draft(client, db):
    card = make_card(next_review_at=local_today())
    db.add(card)
    await db.commit()

    assert (await client.get("/cards/due", headers=API_HEADERS)).json()[0]["resumable"] is False

    db.add(Session(card_id=card.id, question_asked="q", draft_text="half an answer"))
    await db.commit()

    assert (await client.get("/cards/due", headers=API_HEADERS)).json()[0]["resumable"] is True


# --- follow-up gating -------------------------------------------------------


async def test_score_of_two_returns_a_follow_up(client, db, stub_llm):
    score, _ = stub_llm
    score.result = ScoreResult(status="follow_up", follow_up_question="One more — why?")

    card = make_card()
    db.add(card)
    await db.commit()

    start = (await client.post(f"/cards/{card.id}/sessions", headers=API_HEADERS)).json()
    resp = await client.post(
        f"/sessions/{start['session_id']}/answers",
        headers=API_HEADERS,
        json={"text": "a partial answer"},
    )

    assert resp.json() == {"status": "follow_up", "question": "One more — why?"}
    session = await db.get(Session, uuid.UUID(start["session_id"]))
    await db.refresh(session)
    assert session.status == STATUS_AWAITING_FOLLOW_UP
    assert session.follow_up_used is True


async def test_follow_up_used_is_passed_so_a_second_probe_cannot_happen(client, db, stub_llm):
    """Maximum one follow-up per session, enforced by the server."""
    score, calls = stub_llm

    card = make_card()
    db.add(card)
    await db.commit()
    start = (await client.post(f"/cards/{card.id}/sessions", headers=API_HEADERS)).json()

    score.result = ScoreResult(status="follow_up", follow_up_question="One more — why?")
    await client.post(
        f"/sessions/{start['session_id']}/answers", headers=API_HEADERS, json={"text": "partial"}
    )
    assert calls[0]["follow_up_used"] is False

    score.result = ScoreResult(status="complete", score=3, feedback="ok", mastery_summary="s")
    resp = await client.post(
        f"/sessions/{start['session_id']}/answers", headers=API_HEADERS, json={"text": "more"}
    )
    # Second call sees follow_up_used=True, so the scorer cannot probe again.
    assert calls[1]["follow_up_used"] is True
    assert resp.json()["status"] == "complete"


async def test_completing_a_session_applies_sm2_and_updates_the_card(client, db, stub_llm):
    score, _ = stub_llm
    score.result = ScoreResult(
        status="complete", score=4, feedback="Good on ring mechanics.", mastery_summary="solid"
    )

    card = make_card(repetitions=1, interval_days=1, ease_factor=2.5)
    db.add(card)
    await db.commit()
    start = (await client.post(f"/cards/{card.id}/sessions", headers=API_HEADERS)).json()

    body = (
        await client.post(
            f"/sessions/{start['session_id']}/answers", headers=API_HEADERS, json={"text": "answer"}
        )
    ).json()

    assert body["status"] == "complete"
    assert body["score"] == 4
    assert body["interval_days"] == 6  # second successful review

    await db.refresh(card)
    assert card.repetitions == 2
    assert card.last_score == 4
    assert card.mastery_summary == "solid"
    assert card.next_review_at == local_today() + timedelta(days=6)


async def test_answering_a_complete_session_returns_409(client, db, stub_llm):
    card = make_card()
    db.add(card)
    await db.commit()
    start = (await client.post(f"/cards/{card.id}/sessions", headers=API_HEADERS)).json()
    await client.post(
        f"/sessions/{start['session_id']}/answers", headers=API_HEADERS, json={"text": "a"}
    )

    resp = await client.post(
        f"/sessions/{start['session_id']}/answers", headers=API_HEADERS, json={"text": "b"}
    )
    assert resp.status_code == 409


# --- transaction integrity --------------------------------------------------


async def test_llm_failure_mid_scoring_leaves_session_and_card_unchanged(
    client, db, stub_llm, monkeypatch
):
    """A partial write here would leave the card permanently stuck."""
    card = make_card(repetitions=1, interval_days=1, ease_factor=2.5)
    db.add(card)
    await db.commit()
    start = (await client.post(f"/cards/{card.id}/sessions", headers=API_HEADERS)).json()

    async def _boom(**_kwargs):
        raise LLMError("model unreachable")

    monkeypatch.setattr(llm, "score_answer", _boom)
    resp = await client.post(
        f"/sessions/{start['session_id']}/answers", headers=API_HEADERS, json={"text": "answer"}
    )
    # 503, not 500 — the client retries this exact payload.
    assert resp.status_code == 503

    db.expire_all()
    await db.refresh(card)
    session = await db.get(Session, uuid.UUID(start["session_id"]))
    assert card.repetitions == 1
    assert card.last_score is None
    assert card.interval_days == 1
    assert session.status != STATUS_COMPLETE
    assert session.score is None


# --- resume -----------------------------------------------------------------


async def test_starting_a_session_twice_resumes_the_live_one(client, db, stub_llm):
    card = make_card()
    db.add(card)
    await db.commit()

    first = (await client.post(f"/cards/{card.id}/sessions", headers=API_HEADERS)).json()
    assert first["resumed"] is False

    await client.patch(
        f"/sessions/{first['session_id']}/draft",
        headers=API_HEADERS,
        json={"draft_text": "half an answer"},
    )

    second = (await client.post(f"/cards/{card.id}/sessions", headers=API_HEADERS)).json()
    assert second["session_id"] == first["session_id"]
    assert second["resumed"] is True
    assert second["draft_text"] == "half an answer"


# --- cron -------------------------------------------------------------------


async def test_trigger_review_no_ops_outside_the_window(client, db, monkeypatch):
    from datetime import datetime
    from zoneinfo import ZoneInfo

    from app.routers import internal

    monkeypatch.setattr(
        internal,
        "now_in",
        lambda tz: datetime(2026, 7, 24, 3, 0, tzinfo=ZoneInfo(tz)),
    )
    db.add(make_card(next_review_at=local_today()))
    await db.commit()

    body = (await client.post("/internal/trigger-review", headers=CRON_HEADERS)).json()
    assert body == {"sent": False, "reason": "outside_window", "card_id": None, "due_count": None}


async def test_trigger_review_no_ops_when_nothing_due(client, db, in_window):
    db.add(make_card(next_review_at=local_today() + timedelta(days=5)))
    await db.commit()

    body = (await client.post("/internal/trigger-review", headers=CRON_HEADERS)).json()
    assert body["reason"] == "nothing_due"


async def test_trigger_review_never_calls_claude(client, db, in_window, monkeypatch):
    """Generating a question for a push that may never be opened wastes tokens."""

    async def _fail(**_kwargs):
        raise AssertionError("trigger-review must not call the LLM")

    monkeypatch.setattr(llm, "generate_question", _fail)
    monkeypatch.setattr(llm, "score_answer", _fail)

    sent: list[dict] = []

    async def _push(**kwargs):
        sent.append(kwargs)
        return 1

    monkeypatch.setattr(internal, "send_push", _push)

    card = make_card(next_review_at=local_today() - timedelta(days=3))
    db.add(card)
    await db.commit()

    body = (await client.post("/internal/trigger-review", headers=CRON_HEADERS)).json()
    assert body["sent"] is True
    assert body["due_count"] == 1
    assert sent[0]["title"] == "1 due"
    assert sent[0]["body"] == "Consistent hashing"


async def test_undelivered_push_does_not_mark_the_card_as_pushed(
    client, db, in_window, monkeypatch
):
    """A failed delivery must not later be counted against the user.

    last_pushed_at is what check-missed reads to decide a review was ignored.
    Setting it when APNs reached nobody would inflate missed_count with our own
    delivery failures — and missed_count is a signal about the user.
    """

    async def _push_reaching_nobody(**_kwargs):
        return 0

    monkeypatch.setattr(internal, "send_push", _push_reaching_nobody)

    card = make_card(next_review_at=local_today() - timedelta(days=3))
    db.add(card)
    await db.commit()

    body = (await client.post("/internal/trigger-review", headers=CRON_HEADERS)).json()
    assert body["sent"] is False
    assert body["reason"] == "no_devices"
    assert body["due_count"] == 1

    await db.refresh(card)
    assert card.last_pushed_at is None


async def test_check_missed_increments_count_without_touching_ease_factor(client, db):
    """Missing a review is a compliance signal, not a retention signal."""
    from datetime import UTC, datetime

    card = make_card(ease_factor=2.5, last_pushed_at=datetime.now(UTC) - timedelta(hours=6))
    db.add(card)
    await db.commit()

    body = (await client.post("/internal/check-missed", headers=CRON_HEADERS)).json()
    assert body == {"marked_missed": 1}

    await db.refresh(card)
    assert card.missed_count == 1
    assert card.ease_factor == 2.5
