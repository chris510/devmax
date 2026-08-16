import uuid
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from sqlmodel import select

from app.models import (
    STATUS_AWAITING_FOLLOW_UP,
    STATUS_COMPLETE,
    DeviceToken,
    Session,
    SessionProbe,
)
from app.routers import internal
from app.services import ai_consent, llm
from app.services.llm import LLMError, ReattemptResult, ScoreResult
from app.services.push import PushDelivery
from tests.conftest import (
    API_HEADERS,
    CRON_HEADERS,
    local_today,
    local_today_at,
    make_card,
    pin_clock,
)


def completed(mechanism: int, trade_offs: int = 0, failure_modes: int = 0, **kwargs) -> ScoreResult:
    """A complete result built the way `score_answer` builds one: axes first."""
    return ScoreResult(
        status="complete",
        score=llm.derive_composite(mechanism, trade_offs, failure_modes),
        accuracy=mechanism,
        depth=trade_offs,
        boundaries=failure_modes,
        **{"feedback": "Good.", "mastery_summary": "ok", **kwargs},
    )


@pytest.fixture
def stub_llm(monkeypatch):
    """Mock Anthropic entirely — no live calls in CI."""

    questions: list[dict] = []

    async def _question(**kwargs) -> str:
        questions.append(kwargs)
        return f"You're adding a node to a ring. What moves? ({len(questions)})"

    calls: list[dict] = []

    async def _score(**kwargs) -> ScoreResult:
        calls.append(kwargs)
        return _score.result

    _score.result = completed(4, 3)
    _score.questions = questions

    monkeypatch.setattr(llm, "generate_question", _question)
    monkeypatch.setattr(llm, "score_answer", _score)
    return _score, calls


async def probe_rows(db, session_id) -> list[SessionProbe]:
    """This session's scored probes in `idx` order — the transcript's own record."""
    return list(
        (
            await db.exec(
                select(SessionProbe)
                .where(SessionProbe.session_id == session_id)
                .order_by(SessionProbe.idx)
            )
        ).all()
    )


def schedule_of(card):
    return (card.ease_factor, card.interval_days, card.repetitions, card.next_review_at)


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
    score.result = ScoreResult(status="follow_up", follow_up_question="One more: why?")

    card = make_card()
    db.add(card)
    await db.commit()

    start = (await client.post(f"/cards/{card.id}/sessions", headers=API_HEADERS)).json()
    resp = await client.post(
        f"/sessions/{start['session_id']}/answers",
        headers=API_HEADERS,
        json={"text": "a partial answer"},
    )

    assert resp.json() == {
        "status": "follow_up",
        "question": "One more: why?",
        "turn_index": 1,
    }
    session = await db.get(Session, uuid.UUID(start["session_id"]))
    await db.refresh(session)
    assert session.status == STATUS_AWAITING_FOLLOW_UP
    assert session.follow_up_used is True
    # The probe is a row, written unanswered the moment its question is issued.
    probes = await probe_rows(db, session.id)
    assert [(p.idx, p.question, p.answer) for p in probes] == [(1, "One more: why?", "")]


@pytest.mark.parametrize("empty_text", ["", "   \n\t"])
async def test_opening_answer_rejects_empty_text_without_scoring(
    client, db, stub_llm, empty_text, monkeypatch
):
    _score, calls = stub_llm
    card = make_card()
    db.add(card)
    await db.commit()
    started = (await client.post(f"/cards/{card.id}/sessions", headers=API_HEADERS)).json()

    async def consent_must_not_run(*_args, **_kwargs):
        raise AssertionError("empty evidence must fail before provider authorization")

    monkeypatch.setattr(ai_consent, "require_ai_processing", consent_must_not_run)

    response = await client.post(
        f"/sessions/{started['session_id']}/answers",
        headers=API_HEADERS,
        json={"text": empty_text, "turn_index": 0},
    )

    assert response.status_code == 422
    assert response.json()["detail"] == "answer text is empty"
    assert calls == []
    session = await db.get(Session, uuid.UUID(started["session_id"]))
    await db.refresh(session)
    assert session.status == "open"
    assert session.answer_text == ""
    assert await probe_rows(db, session.id) == []


async def test_probe_answer_rejects_empty_text_without_advancing(
    client, db, stub_llm, monkeypatch
):
    score, calls = stub_llm
    score.result = ScoreResult(status="follow_up", follow_up_question="One more — why?")
    card = make_card()
    db.add(card)
    await db.commit()
    started = (await client.post(f"/cards/{card.id}/sessions", headers=API_HEADERS)).json()
    endpoint = f"/sessions/{started['session_id']}/answers"
    assert (
        await client.post(
            endpoint,
            headers=API_HEADERS,
            json={"text": "initial evidence", "turn_index": 0},
        )
    ).status_code == 200

    async def consent_must_not_run(*_args, **_kwargs):
        raise AssertionError("empty evidence must fail before provider authorization")

    monkeypatch.setattr(ai_consent, "require_ai_processing", consent_must_not_run)

    response = await client.post(
        endpoint,
        headers=API_HEADERS,
        json={"text": " \n ", "turn_index": 1},
    )

    assert response.status_code == 422
    assert response.json()["detail"] == "answer text is empty"
    assert len(calls) == 1
    session = await db.get(Session, uuid.UUID(started["session_id"]))
    await db.refresh(session)
    assert session.status == STATUS_AWAITING_FOLLOW_UP
    assert session.answer_text == "initial evidence"
    probes = await probe_rows(db, session.id)
    assert [(probe.idx, probe.answer) for probe in probes] == [(1, "")]


async def test_exact_initial_answer_replay_returns_the_committed_probe_without_rescoring(
    client, db, stub_llm
):
    """A lost follow-up response cannot turn one answer into two scored turns."""
    score, calls = stub_llm
    score.result = ScoreResult(status="follow_up", follow_up_question="One more: why?")
    card = make_card(repetitions=1, interval_days=6, ease_factor=2.36)
    db.add(card)
    await db.commit()
    schedule_before = schedule_of(card)

    start = (await client.post(f"/cards/{card.id}/sessions", headers=API_HEADERS)).json()
    endpoint = f"/sessions/{start['session_id']}/answers"
    payload = {"text": "the exact saved initial answer"}
    first = await client.post(endpoint, headers=API_HEADERS, json=payload)
    replay = await client.post(endpoint, headers=API_HEADERS, json=payload)

    assert first.json() == replay.json() == {
        "status": "follow_up",
        "question": "One more: why?",
        "turn_index": 1,
    }
    assert len(calls) == 1
    assert calls[0]["probes"] == []
    session = await db.get(Session, uuid.UUID(start["session_id"]))
    await db.refresh(session)
    await db.refresh(card)
    assert session.status == STATUS_AWAITING_FOLLOW_UP
    assert session.answer_text == payload["text"]
    probes = await probe_rows(db, session.id)
    assert [(p.idx, p.answer) for p in probes] == [(1, "")]
    assert schedule_of(card) == schedule_before

    # A genuinely new turn still follows the existing V1 completion path.
    score.result = completed(3, mastery_summary="recovered after a probe")
    completed_response = await client.post(
        endpoint, headers=API_HEADERS, json={"text": "the missing causal link"}
    )
    assert completed_response.json()["status"] == "complete"
    assert len(calls) == 2
    assert calls[1]["probes"] == [("One more: why?", "the missing causal link")]


async def test_turn_index_disambiguates_the_same_text_on_adjacent_turns(
    client, db, stub_llm
):
    """Repeated words can be new evidence; the question turn, not text, decides."""
    score, calls = stub_llm
    score.result = ScoreResult(status="follow_up", follow_up_question="One more — why?")
    card = make_card()
    db.add(card)
    await db.commit()
    started = (await client.post(f"/cards/{card.id}/sessions", headers=API_HEADERS)).json()
    endpoint = f"/sessions/{started['session_id']}/answers"
    repeated_text = "I would still answer it this way"

    future = await client.post(
        endpoint,
        headers=API_HEADERS,
        json={"text": repeated_text, "turn_index": 1},
    )
    assert future.status_code == 409
    assert calls == []

    first = await client.post(
        endpoint,
        headers=API_HEADERS,
        json={"text": repeated_text, "turn_index": 0},
    )
    assert first.json()["turn_index"] == 1

    past_replay = await client.post(
        endpoint,
        headers=API_HEADERS,
        json={"text": repeated_text, "turn_index": 0},
    )
    assert past_replay.json() == first.json()
    assert len(calls) == 1

    reused_index = await client.post(
        endpoint,
        headers=API_HEADERS,
        json={"text": "changed after the commit", "turn_index": 0},
    )
    assert reused_index.status_code == 409
    assert len(calls) == 1

    future = await client.post(
        endpoint,
        headers=API_HEADERS,
        json={"text": repeated_text, "turn_index": 2},
    )
    assert future.status_code == 409
    assert len(calls) == 1

    score.result = completed(4, mastery_summary="made the link on the probe")
    second = await client.post(
        endpoint,
        headers=API_HEADERS,
        json={"text": repeated_text, "turn_index": 1},
    )

    assert second.json()["status"] == "complete"
    assert len(calls) == 2
    assert calls[1]["probes"] == [("One more — why?", repeated_text)]


async def test_exact_terminal_answer_replay_returns_the_committed_result_without_rescoring(
    client, db, stub_llm, monkeypatch
):
    """A lost complete response is recoverable without a second schedule write."""
    score, calls = stub_llm
    score.result = completed(4, 3, mastery_summary="solid causal account")
    card = make_card(repetitions=1, interval_days=6, ease_factor=2.36)
    db.add(card)
    await db.commit()

    started = (await client.post(f"/cards/{card.id}/sessions", headers=API_HEADERS)).json()
    endpoint = f"/sessions/{started['session_id']}/answers"
    payload = {"text": "the exact terminal answer saved on disk", "turn_index": 0}
    first = await client.post(endpoint, headers=API_HEADERS, json=payload)
    await db.refresh(card)
    schedule_after_first = schedule_of(card)

    async def consent_must_not_run(*_args, **_kwargs):
        raise AssertionError("an already-committed replay must not re-authorize AI")

    monkeypatch.setattr(ai_consent, "require_ai_processing", consent_must_not_run)
    replay = await client.post(endpoint, headers=API_HEADERS, json=payload)

    assert first.status_code == replay.status_code == 200
    assert replay.json() == first.json()
    assert len(calls) == 1
    await db.refresh(card)
    assert schedule_of(card) == schedule_after_first


async def test_terminal_answer_replay_expires_after_a_later_review(
    client, db, stub_llm
):
    score, calls = stub_llm
    score.result = completed(4, mastery_summary="first review")
    card = make_card()
    db.add(card)
    await db.commit()

    first = (await client.post(f"/cards/{card.id}/sessions", headers=API_HEADERS)).json()
    first_endpoint = f"/sessions/{first['session_id']}/answers"
    first_payload = {"text": "first answer", "turn_index": 0}
    assert (
        await client.post(first_endpoint, headers=API_HEADERS, json=first_payload)
    ).status_code == 200

    score.result = completed(5, mastery_summary="newer review")
    second = (await client.post(f"/cards/{card.id}/sessions", headers=API_HEADERS)).json()
    assert (
        await client.post(
            f"/sessions/{second['session_id']}/answers",
            headers=API_HEADERS,
            json={"text": "newer answer", "turn_index": 0},
        )
    ).status_code == 200

    stale_replay = await client.post(
        first_endpoint, headers=API_HEADERS, json=first_payload
    )

    assert stale_replay.status_code == 409
    assert stale_replay.json()["detail"] == "session result has been superseded"
    assert len(calls) == 2


async def test_replaying_the_first_probe_answer_returns_the_second_probe_unscored(
    client, db, stub_llm
):
    """The replay guard covers every probe, not only the first.

    Turn 2's response can be lost exactly like turn 1's. Scoring the resent text
    as turn 3's answer would spend the last scored turn on evidence already used.
    """
    score, calls = stub_llm
    card = make_card(repetitions=1, interval_days=6, ease_factor=2.36)
    db.add(card)
    await db.commit()
    schedule_before = schedule_of(card)
    start = (await client.post(f"/cards/{card.id}/sessions", headers=API_HEADERS)).json()
    endpoint = f"/sessions/{start['session_id']}/answers"

    score.result = ScoreResult(status="follow_up", follow_up_question="One more: why?")
    await client.post(endpoint, headers=API_HEADERS, json={"text": "a partial answer"})

    score.result = ScoreResult(status="follow_up", follow_up_question="Last one: how?")
    payload = {"text": "the probe answer that was saved"}
    first = await client.post(endpoint, headers=API_HEADERS, json=payload)
    replay = await client.post(endpoint, headers=API_HEADERS, json=payload)

    assert first.json() == replay.json() == {
        "status": "follow_up",
        "question": "Last one: how?",
        "turn_index": 2,
    }
    assert len(calls) == 2

    oldest_replay = await client.post(
        endpoint,
        headers=API_HEADERS,
        json={"text": "a partial answer", "turn_index": 0},
    )
    assert oldest_replay.json() == {
        "status": "follow_up",
        "question": "One more: why?",
        "turn_index": 1,
    }
    changed_oldest = await client.post(
        endpoint,
        headers=API_HEADERS,
        json={"text": "changed initial evidence", "turn_index": 0},
    )
    assert changed_oldest.status_code == 409
    assert len(calls) == 2

    session = await db.get(Session, uuid.UUID(start["session_id"]))
    await db.refresh(session)
    await db.refresh(card)
    assert session.status == STATUS_AWAITING_FOLLOW_UP
    assert [(p.idx, p.question, p.answer) for p in await probe_rows(db, session.id)] == [
        (1, "One more: why?", payload["text"]),
        (2, "Last one: how?", ""),
    ]
    assert schedule_of(card) == schedule_before


async def test_two_scored_follow_ups_are_the_cap_and_the_third_turn_completes(
    client, db, stub_llm
):
    """`MAX_SCORED_FOLLOW_UPS` scored probes per session, enforced by the server.

    The scorer sees the whole transcript so far in `probes`, one pair longer each
    turn; after the cap there is no turn left to submit against.
    """
    score, calls = stub_llm
    card = make_card()
    db.add(card)
    await db.commit()
    start = (await client.post(f"/cards/{card.id}/sessions", headers=API_HEADERS)).json()
    endpoint = f"/sessions/{start['session_id']}/answers"

    score.result = ScoreResult(status="follow_up", follow_up_question="One more: why?")
    await client.post(endpoint, headers=API_HEADERS, json={"text": "partial"})

    score.result = ScoreResult(status="follow_up", follow_up_question="Last one: how?")
    await client.post(endpoint, headers=API_HEADERS, json={"text": "still partial"})

    score.result = completed(3, mastery_summary="recovered after a probe")
    final = await client.post(endpoint, headers=API_HEADERS, json={"text": "the missing link"})
    assert final.json()["status"] == "complete"

    exact_replay = await client.post(
        endpoint,
        headers=API_HEADERS,
        json={"text": "the missing link", "turn_index": 2},
    )
    assert exact_replay.status_code == 200
    assert exact_replay.json() == final.json()
    first_turn_replay = await client.post(
        endpoint, headers=API_HEADERS, json={"text": "partial", "turn_index": 0}
    )
    second_turn_replay = await client.post(
        endpoint,
        headers=API_HEADERS,
        json={"text": "still partial", "turn_index": 1},
    )
    assert first_turn_replay.json() == {
        "status": "follow_up",
        "question": "One more: why?",
        "turn_index": 1,
    }
    assert second_turn_replay.json() == {
        "status": "follow_up",
        "question": "Last one: how?",
        "turn_index": 2,
    }

    assert llm.MAX_SCORED_FOLLOW_UPS == 2
    assert [call["probes"] for call in calls] == [
        [],
        [("One more: why?", "still partial")],
        [
            ("One more: why?", "still partial"),
            ("Last one: how?", "the missing link"),
        ],
    ]

    # Exact retransmission recovers the terminal response, but there is no fourth
    # turn to take with genuinely new evidence.
    fourth = await client.post(endpoint, headers=API_HEADERS, json={"text": "one more thought"})
    assert fourth.status_code == 409
    assert len(calls) == 3


async def test_a_probe_past_the_cap_is_refused_at_the_write_site(client, db, stub_llm):
    """The router re-checks the cap the parsers already enforce.

    A parser bug that let a third probe through must not be able to extend a
    session — so a follow-up returned at the cap fails the request instead, and
    leaves the session and card exactly as they were.
    """
    score, calls = stub_llm
    card = make_card(repetitions=1, interval_days=6, ease_factor=2.36)
    db.add(card)
    await db.commit()
    session = Session(
        card_id=card.id,
        question_asked="What moves when a node joins?",
        answer_text="the initial answer",
        follow_up_used=True,
        status=STATUS_AWAITING_FOLLOW_UP,
    )
    db.add(session)
    db.add(SessionProbe(session_id=session.id, idx=1, question="One more: why?", answer="first"))
    db.add(SessionProbe(session_id=session.id, idx=2, question="Last one: how?"))
    await db.commit()
    schedule_before = schedule_of(card)

    score.result = ScoreResult(status="follow_up", follow_up_question="Actually, one more?")
    resp = await client.post(
        f"/sessions/{session.id}/answers", headers=API_HEADERS, json={"text": "second"}
    )

    assert resp.status_code == 503
    assert resp.json() == {"detail": "scoring_unavailable"}
    assert calls[0]["probes"] == [
        ("One more: why?", "first"),
        ("Last one: how?", "second"),
    ]
    db.expire_all()
    await db.refresh(card)
    await db.refresh(session)
    assert schedule_of(card) == schedule_before
    assert card.last_score is None
    assert card.last_reviewed_at is None
    assert session.status == STATUS_AWAITING_FOLLOW_UP
    assert session.score is None
    assert [(p.idx, p.answer) for p in await probe_rows(db, session.id)] == [
        (1, "first"),
        (2, ""),
    ]


async def test_an_insufficiency_probe_defers_sm2_until_the_final_turn(client, db, stub_llm):
    """Probe 1 by band, probe 2 by insufficiency, one SM-2 application at the end."""
    score, _ = stub_llm
    card = make_card(repetitions=1, interval_days=1, ease_factor=2.5)
    db.add(card)
    await db.commit()
    schedule_before = schedule_of(card)
    start = (await client.post(f"/cards/{card.id}/sessions", headers=API_HEADERS)).json()
    endpoint = f"/sessions/{start['session_id']}/answers"
    session_id = uuid.UUID(start["session_id"])

    score.result = ScoreResult(status="follow_up", follow_up_question="One more: why?")
    await client.post(endpoint, headers=API_HEADERS, json={"text": "a partial answer"})

    score.result = ScoreResult(status="follow_up", follow_up_question="Last one: how?")
    second = await client.post(endpoint, headers=API_HEADERS, json={"text": "still thin"})
    assert second.json() == {
        "status": "follow_up",
        "question": "Last one: how?",
        "turn_index": 2,
    }

    session = await db.get(Session, session_id)
    await db.refresh(session)
    await db.refresh(card)
    assert session.status == STATUS_AWAITING_FOLLOW_UP
    assert session.score is None
    assert [(p.idx, p.question, p.answer) for p in await probe_rows(db, session_id)] == [
        (1, "One more: why?", "still thin"),
        (2, "Last one: how?", ""),
    ]
    assert schedule_of(card) == schedule_before

    score.result = completed(4, 3, mastery_summary="recovered after a probe")
    final = (
        await client.post(endpoint, headers=API_HEADERS, json={"text": "the missing link"})
    ).json()

    assert final["status"] == "complete"
    # One SM-2 application, from the final turn's Accuracy: the `good` bucket on a
    # second successful review. Two applications would have taken it past 6 days.
    assert final["interval_days"] == 6
    await db.refresh(card)
    await db.refresh(session)
    assert schedule_of(card) == (2.5, 6, 2, local_today() + timedelta(days=6))
    assert session.status == STATUS_COMPLETE
    assert session.accuracy == 4
    assert [p.answer for p in await probe_rows(db, session_id)] == [
        "still thin",
        "the missing link",
    ]


async def test_resuming_mid_second_probe_returns_that_probe(client, db, stub_llm):
    """Resume shows the turn the session is actually waiting on."""
    score, _ = stub_llm
    card = make_card()
    db.add(card)
    await db.commit()
    start = (await client.post(f"/cards/{card.id}/sessions", headers=API_HEADERS)).json()
    endpoint = f"/sessions/{start['session_id']}/answers"

    score.result = ScoreResult(status="follow_up", follow_up_question="One more: why?")
    await client.post(endpoint, headers=API_HEADERS, json={"text": "partial"})
    resumed = (await client.post(f"/cards/{card.id}/sessions", headers=API_HEADERS)).json()
    assert resumed["question"] == "One more: why?"

    score.result = ScoreResult(status="follow_up", follow_up_question="Last one: how?")
    await client.post(endpoint, headers=API_HEADERS, json={"text": "still partial"})

    resumed = (await client.post(f"/cards/{card.id}/sessions", headers=API_HEADERS)).json()
    assert resumed["session_id"] == start["session_id"]
    assert resumed["question"] == "Last one: how?"
    assert resumed["is_follow_up"] is True
    assert resumed["resumed"] is True


async def test_card_history_renders_both_probes_in_order(client, db, stub_llm):
    score, _ = stub_llm
    card = make_card()
    db.add(card)
    await db.commit()
    start = (await client.post(f"/cards/{card.id}/sessions", headers=API_HEADERS)).json()
    endpoint = f"/sessions/{start['session_id']}/answers"

    score.result = ScoreResult(status="follow_up", follow_up_question="One more: why?")
    await client.post(endpoint, headers=API_HEADERS, json={"text": "the first answer"})
    score.result = ScoreResult(status="follow_up", follow_up_question="Last one: how?")
    await client.post(endpoint, headers=API_HEADERS, json={"text": "the second answer"})
    score.result = completed(3, feedback="Recovered the link.", mastery_summary="ok")
    await client.post(endpoint, headers=API_HEADERS, json={"text": "the third answer"})

    detail = (await client.get(f"/cards/{card.id}", headers=API_HEADERS)).json()
    turns = detail["sessions"][0]["turns"]
    assert [turn["role"] for turn in turns] == [
        "question",
        "answer",
        "follow_up",
        "answer",
        "follow_up",
        "answer",
        "score",
    ]
    assert [turn["text"] for turn in turns[1:6]] == [
        "the first answer",
        "One more: why?",
        "the second answer",
        "Last one: how?",
        "the third answer",
    ]
    assert turns[6]["text"] == "3 · Recovered the link."


async def test_completing_a_session_applies_sm2_and_updates_the_card(client, db, stub_llm):
    score, _ = stub_llm
    score.result = completed(4, 3, feedback="Good on ring mechanics.", mastery_summary="solid")

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
    # Denormalised alongside last_score so Coverage's rollup is one query.
    assert card.last_accuracy == 4
    assert card.last_depth == 3
    assert card.last_boundaries == 0
    assert card.last_reviewed_at is not None


# --- mechanism-gated scheduling ---------------------------------------------
# The composite score is a display concern. What reschedules a card is whether
# the mechanism was reconstructed — nothing else.


async def test_scheduling_gates_on_mechanism_not_the_composite(client, db, stub_llm):
    """A thin-but-correct answer and a complete one move the card identically.

    Composite 3 vs 5. Under the old blended quality those produced different ease
    deltas, which let "knew it, didn't volunteer the failure modes" drag the
    interval down on a topic the user actually knows.
    """
    score, _ = stub_llm
    eases = []

    for result in (completed(3), completed(5, 5, 5)):
        card = make_card(repetitions=1, interval_days=1, ease_factor=2.5)
        db.add(card)
        await db.commit()
        start = (await client.post(f"/cards/{card.id}/sessions", headers=API_HEADERS)).json()

        score.result = result
        body = (
            await client.post(
                f"/sessions/{start['session_id']}/answers", headers=API_HEADERS, json={"text": "a"}
            )
        ).json()

        await db.refresh(card)
        eases.append(card.ease_factor)
        assert body["interval_days"] == 6
        assert card.repetitions == 2

    assert eases[0] == eases[1]


async def test_a_wrong_mechanism_fails_the_card_however_deep_the_rest(client, db, stub_llm):
    """Depth cannot rescue a broken mechanism — the composite caps at the axis."""
    score, _ = stub_llm
    score.result = completed(2, 5, 5)

    card = make_card(repetitions=3, interval_days=12, ease_factor=2.5)
    db.add(card)
    await db.commit()
    start = (await client.post(f"/cards/{card.id}/sessions", headers=API_HEADERS)).json()

    body = (
        await client.post(
            f"/sessions/{start['session_id']}/answers", headers=API_HEADERS, json={"text": "a"}
        )
    ).json()

    assert body["score"] == 2
    await db.refresh(card)
    assert card.repetitions == 0
    assert card.interval_days == 1
    assert card.ease_factor < 2.5


async def test_missed_count_still_never_reaches_the_ease_factor(client, db, stub_llm):
    """Unchanged by the gate rewrite — compliance is not retention."""
    score, _ = stub_llm
    score.result = completed(5, 5, 5)

    card = make_card(repetitions=1, interval_days=1, ease_factor=2.5, missed_count=9)
    db.add(card)
    await db.commit()
    start = (await client.post(f"/cards/{card.id}/sessions", headers=API_HEADERS)).json()
    await client.post(
        f"/sessions/{start['session_id']}/answers", headers=API_HEADERS, json={"text": "a"}
    )

    await db.refresh(card)
    assert card.missed_count == 9
    assert card.ease_factor == 2.5  # a "good" rating is ease-neutral


# --- the canonical question -------------------------------------------------


async def test_the_first_session_generates_and_persists_the_question(client, db, stub_llm):
    score, _ = stub_llm
    card = make_card()
    db.add(card)
    await db.commit()

    start = (await client.post(f"/cards/{card.id}/sessions", headers=API_HEADERS)).json()

    await db.refresh(card)
    assert card.canonical_question == start["question"]
    assert len(score.questions) == 1


async def test_later_sessions_reuse_the_question_without_calling_the_model(client, db, stub_llm):
    """Testing the same retrieval repeatedly is the point; it also saves a call."""
    score, _ = stub_llm
    card = make_card()
    db.add(card)
    await db.commit()

    first = (await client.post(f"/cards/{card.id}/sessions", headers=API_HEADERS)).json()
    await client.post(
        f"/sessions/{first['session_id']}/answers", headers=API_HEADERS, json={"text": "a"}
    )
    second = (await client.post(f"/cards/{card.id}/sessions", headers=API_HEADERS)).json()

    assert second["question"] == first["question"]
    assert second["session_id"] != first["session_id"]
    assert len(score.questions) == 1  # no second generation call


async def test_clearing_the_column_re_rolls_the_question(client, db, stub_llm):
    """The documented escape hatch for a badly worded canonical question."""
    score, _ = stub_llm
    card = make_card()
    db.add(card)
    await db.commit()

    first = (await client.post(f"/cards/{card.id}/sessions", headers=API_HEADERS)).json()
    await client.post(
        f"/sessions/{first['session_id']}/answers", headers=API_HEADERS, json={"text": "a"}
    )
    await db.refresh(card)
    card.canonical_question = None
    db.add(card)
    await db.commit()

    second = (await client.post(f"/cards/{card.id}/sessions", headers=API_HEADERS)).json()

    assert second["question"] != first["question"]
    assert len(score.questions) == 2


# --- practice mode ----------------------------------------------------------


async def test_a_practice_session_scores_without_touching_the_schedule(client, db, stub_llm):
    """Review Sprint: scores land in history, the review schedule does not move."""
    score, _ = stub_llm
    score.result = completed(5, 5, 5, mastery_summary="solid all round")

    due = local_today() + timedelta(days=9)
    card = make_card(repetitions=3, interval_days=12, ease_factor=2.4, next_review_at=due)
    db.add(card)
    await db.commit()

    start = (
        await client.post(f"/cards/{card.id}/sessions?practice=true", headers=API_HEADERS)
    ).json()
    body = (
        await client.post(
            f"/sessions/{start['session_id']}/answers", headers=API_HEADERS, json={"text": "a"}
        )
    ).json()

    assert body["practice"] is True
    # The schedule fields echo the card's untouched values.
    assert body["interval_days"] == 12
    assert body["next_review_at"] == due.isoformat()

    await db.refresh(card)
    assert (card.interval_days, card.repetitions, card.ease_factor) == (12, 3, 2.4)
    assert card.next_review_at == due
    # Mastery signal is real and is written.
    assert card.last_score == 5
    assert card.mastery_summary == "solid all round"

    detail = (await client.get(f"/cards/{card.id}", headers=API_HEADERS)).json()
    assert len(detail["sessions"]) == 1
    assert detail["sessions"][0]["score"] == 5


async def test_a_practice_session_is_not_the_default(client, db, stub_llm):
    card = make_card(repetitions=1, interval_days=1)
    db.add(card)
    await db.commit()
    start = (await client.post(f"/cards/{card.id}/sessions", headers=API_HEADERS)).json()

    body = (
        await client.post(
            f"/sessions/{start['session_id']}/answers", headers=API_HEADERS, json={"text": "a"}
        )
    ).json()

    assert body["practice"] is False
    await db.refresh(card)
    assert card.interval_days == 6


@pytest.mark.parametrize("existing_practice", [False, True])
async def test_live_session_resume_rejects_the_opposite_mode(
    client, db, stub_llm, existing_practice
):
    card = make_card(canonical_question="How does the ring move keys?")
    db.add(card)
    await db.commit()

    suffix = "?practice=true" if existing_practice else ""
    first = await client.post(f"/cards/{card.id}/sessions{suffix}", headers=API_HEADERS)
    assert first.status_code == 200
    assert first.json()["practice"] is existing_practice

    opposite_suffix = "" if existing_practice else "?practice=true"
    conflict = await client.post(
        f"/cards/{card.id}/sessions{opposite_suffix}", headers=API_HEADERS
    )
    assert conflict.status_code == 409
    assert conflict.json()["detail"] == {
        "code": "session_mode_conflict",
        "session_id": first.json()["session_id"],
        "practice": existing_practice,
    }

    resumed = await client.post(f"/cards/{card.id}/sessions{suffix}", headers=API_HEADERS)
    assert resumed.status_code == 200
    assert resumed.json()["session_id"] == first.json()["session_id"]
    assert resumed.json()["resumed"] is True
    assert resumed.json()["practice"] is existing_practice


async def test_abandon_is_idempotent_preserves_draft_and_never_moves_schedule(
    client, db, stub_llm
):
    due = local_today() + timedelta(days=5)
    card = make_card(
        canonical_question="How does the ring move keys?",
        repetitions=3,
        interval_days=9,
        next_review_at=due,
    )
    db.add(card)
    await db.commit()
    start = (
        await client.post(f"/cards/{card.id}/sessions", headers=API_HEADERS)
    ).json()
    await client.patch(
        f"/sessions/{start['session_id']}/draft",
        headers=API_HEADERS,
        json={"draft_text": "unfinished but durable"},
    )

    first = await client.post(
        f"/sessions/{start['session_id']}/abandon", headers=API_HEADERS
    )
    replay = await client.post(
        f"/sessions/{start['session_id']}/abandon", headers=API_HEADERS
    )
    assert first.status_code == replay.status_code == 204

    await db.refresh(card)
    abandoned = await db.get(Session, uuid.UUID(start["session_id"]))
    assert abandoned is not None
    assert abandoned.status == "abandoned"
    assert abandoned.ended_at is not None
    assert abandoned.draft_text == "unfinished but durable"
    assert (card.repetitions, card.interval_days, card.next_review_at) == (3, 9, due)

    stale_submit = await client.post(
        f"/sessions/{start['session_id']}/answers",
        headers=API_HEADERS,
        json={"text": "this must not be scored"},
    )
    assert stale_submit.status_code == 409
    assert stale_submit.json()["detail"] == "session is abandoned"

    practice = await client.post(
        f"/cards/{card.id}/sessions?practice=true", headers=API_HEADERS
    )
    assert practice.status_code == 200
    assert practice.json()["session_id"] != start["session_id"]
    assert practice.json()["practice"] is True


# --- the card library -------------------------------------------------------
# Review Sprint Setup and Coverage both read GET /cards, so the fields they need
# are computed server-side rather than re-derived on the client.


async def test_the_library_carries_due_labels_and_axis_scores(client, db, stub_llm):
    score, _ = stub_llm
    score.result = completed(4, 3, 1)

    card = make_card(next_review_at=local_today() - timedelta(days=3))
    db.add(card)
    await db.commit()

    row = (await client.get("/cards", headers=API_HEADERS)).json()[0]
    assert row["due_label"] == "3 days overdue"
    assert row["days_since_review"] is None
    assert row["last_accuracy"] is None

    start = (await client.post(f"/cards/{card.id}/sessions", headers=API_HEADERS)).json()
    await client.post(
        f"/sessions/{start['session_id']}/answers", headers=API_HEADERS, json={"text": "a"}
    )

    row = (await client.get("/cards", headers=API_HEADERS)).json()[0]
    assert row["days_since_review"] == 0
    assert row["last_accuracy"] == 4
    assert row["last_depth"] == 3
    assert row["last_boundaries"] == 1


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


async def test_answer_fails_closed_if_a_live_session_points_at_an_archived_card(
    client, db, stub_llm
):
    """Defense in depth for a row created by an older maintenance/start race."""
    _, calls = stub_llm
    card = make_card(lifecycle_status="archived")
    session = Session(
        card_id=card.id,
        question_asked="What moves?",
        status="open",
    )
    db.add(card)
    db.add(session)
    await db.commit()

    response = await client.post(
        f"/sessions/{session.id}/answers",
        headers=API_HEADERS,
        json={"text": "an answer", "turn_index": 0},
    )

    assert response.status_code == 409
    assert response.json()["detail"] == "card is archived"
    assert calls == []
    await db.refresh(session)
    assert session.status == "open"
    assert session.answer_text == ""


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


async def test_turn_aware_draft_ignores_past_and_future_uploads(
    client, db, stub_llm
):
    score, _ = stub_llm
    score.result = ScoreResult(status="follow_up", follow_up_question="One more — why?")
    card = make_card()
    db.add(card)
    await db.commit()
    started = (await client.post(f"/cards/{card.id}/sessions", headers=API_HEADERS)).json()
    session_id = uuid.UUID(started["session_id"])
    draft_endpoint = f"/sessions/{session_id}/draft"

    current = await client.patch(
        draft_endpoint,
        headers=API_HEADERS,
        json={"draft_text": "opening partial", "turn_index": 0},
    )
    assert current.status_code == 204
    db.expire_all()
    assert (await db.get(Session, session_id)).draft_text == "opening partial"

    await client.post(
        f"/sessions/{session_id}/answers",
        headers=API_HEADERS,
        json={"text": "opening answer", "turn_index": 0},
    )
    stale = await client.patch(
        draft_endpoint,
        headers=API_HEADERS,
        json={"draft_text": "late opening partial", "turn_index": 0},
    )
    future = await client.patch(
        draft_endpoint,
        headers=API_HEADERS,
        json={"draft_text": "future partial", "turn_index": 2},
    )
    assert stale.status_code == future.status_code == 204
    db.expire_all()
    assert (await db.get(Session, session_id)).draft_text == ""

    current = await client.patch(
        draft_endpoint,
        headers=API_HEADERS,
        json={"draft_text": "probe partial", "turn_index": 1},
    )
    assert current.status_code == 204
    db.expire_all()
    assert (await db.get(Session, session_id)).draft_text == "probe partial"


async def test_completed_session_draft_is_acknowledged_without_storage(
    client, db, stub_llm
):
    """Post-result turns are local-only because no server reopen path exists."""
    card = make_card()
    db.add(card)
    await db.commit()
    started = (await client.post(f"/cards/{card.id}/sessions", headers=API_HEADERS)).json()
    session_id = uuid.UUID(started["session_id"])
    await client.post(
        f"/sessions/{session_id}/answers",
        headers=API_HEADERS,
        json={"text": "completed answer", "turn_index": 0},
    )

    indexed = await client.patch(
        f"/sessions/{session_id}/draft",
        headers=API_HEADERS,
        json={"draft_text": "post-result partial", "turn_index": 0},
    )
    legacy = await client.patch(
        f"/sessions/{session_id}/draft",
        headers=API_HEADERS,
        json={"draft_text": "legacy post-result partial"},
    )

    assert indexed.status_code == legacy.status_code == 204
    db.expire_all()
    assert (await db.get(Session, session_id)).draft_text == ""


async def test_draft_and_answer_transcripts_are_bounded_and_answers_are_nonblank(
    client, db, stub_llm
):
    card = make_card(canonical_question="What moves on the ring?")
    db.add(card)
    await db.commit()
    start = (
        await client.post(f"/cards/{card.id}/sessions", headers=API_HEADERS)
    ).json()

    oversized_draft = await client.patch(
        f"/sessions/{start['session_id']}/draft",
        headers=API_HEADERS,
        json={"draft_text": "x" * 20_001},
    )
    assert oversized_draft.status_code == 422

    for text in ("   ", "x" * 20_001):
        response = await client.post(
            f"/sessions/{start['session_id']}/answers",
            headers=API_HEADERS,
            json={"text": text},
        )
        assert response.status_code == 422


# --- cron -------------------------------------------------------------------


@pytest.fixture
def capture_push(monkeypatch):
    """Records every push instead of sending one, and reports it reached a device."""
    sent: list[dict] = []

    async def _push(**kwargs):
        sent.append(kwargs)
        return PushDelivery(sent=1, attempted=1)

    monkeypatch.setattr(internal, "send_push", _push)
    return sent


async def test_a_second_poll_inside_the_same_window_does_not_push_again(
    client, db, in_window, capture_push
):
    """The runtime polls every 15 minutes; the window decides, not the poll.

    Without a per-window guard an 80-minute window takes several polls, and the
    day's whole `reviews_per_day` budget is spent before evening opens.
    """
    db.add(make_card(next_review_at=local_today() - timedelta(days=3)))
    db.add(make_card(topic="Raft", next_review_at=local_today() - timedelta(days=2)))
    await db.commit()

    first = (await client.post("/internal/trigger-review", headers=CRON_HEADERS)).json()
    second = (await client.post("/internal/trigger-review", headers=CRON_HEADERS)).json()

    assert first["sent"] is True
    assert second == {
        "sent": False,
        "reason": "already_pushed",
        "card_id": None,
        "due_count": None,
    }
    assert len(capture_push) == 1


async def test_a_morning_push_does_not_block_the_evening_window(
    client, db, monkeypatch, capture_push
):
    """Each window gets its own push. The guard is per window, not per day."""
    db.add(make_card(topic="Pushed this morning", last_pushed_at=local_today_at(7, 15)))
    db.add(make_card(topic="Raft", next_review_at=local_today() - timedelta(days=2)))
    await db.commit()

    pin_clock(monkeypatch, 21, 30)
    body = (await client.post("/internal/trigger-review", headers=CRON_HEADERS)).json()

    assert body["sent"] is True
    assert capture_push[0]["body"] == "Raft"


async def test_the_same_card_is_not_offered_twice_in_one_day(client, db, monkeypatch, capture_push):
    """One unanswered card is not worth two notifications.

    The evening window takes the next card down, or stays quiet — it never
    repeats the morning's.
    """
    db.add(
        make_card(
            next_review_at=local_today() - timedelta(days=3),
            last_pushed_at=local_today_at(7, 15),
        )
    )
    await db.commit()

    pin_clock(monkeypatch, 21, 30)
    body = (await client.post("/internal/trigger-review", headers=CRON_HEADERS)).json()

    # `due_count` still reports the whole queue — it is the notification's "N due"
    # and has to agree with GET /cards/due. Only the selection skips the card.
    assert body["reason"] == "already_offered"
    assert body["due_count"] == 1
    assert capture_push == []


async def test_the_daily_budget_is_a_backstop_behind_the_window_guard(
    client, db, monkeypatch, capture_push
):
    """`reviews_per_day` is 2, so a third push is refused even in a fresh window."""
    db.add(make_card(topic="First", last_pushed_at=local_today_at(7, 15)))
    db.add(make_card(topic="Second", last_pushed_at=local_today_at(7, 45)))
    db.add(make_card(topic="Raft", next_review_at=local_today() - timedelta(days=2)))
    await db.commit()

    pin_clock(monkeypatch, 21, 30)
    body = (await client.post("/internal/trigger-review", headers=CRON_HEADERS)).json()

    assert body["reason"] == "daily_limit"
    assert capture_push == []


async def test_a_missed_push_still_counts_against_the_daily_budget(
    client, db, monkeypatch, capture_push
):
    """The bug migration 0004 exists to fix, end to end.

    check-missed used to clear `last_pushed_at` after counting a review missed,
    which handed the day's budget straight back: ignore the morning push and the
    evening one went out anyway, on top of a `reviews_per_day` of 1.
    """
    from app.models import Settings

    settings = await db.get(Settings, 1)
    settings.reviews_per_day = 1
    db.add(settings)
    db.add(make_card(topic="Ignored", last_pushed_at=local_today_at(7, 15)))
    db.add(make_card(topic="Raft", next_review_at=local_today() - timedelta(days=2)))
    await db.commit()

    # check-missed reads the real clock, not the pinned one, so its four-hour
    # cutoff would only clear the morning stamp when the suite happens to run
    # after 11:15 local. Widening it keeps the test about the budget rather than
    # about the hour it runs at.
    monkeypatch.setattr(internal, "MISSED_AFTER", timedelta(days=-1))
    marked = (await client.post("/internal/check-missed", headers=CRON_HEADERS)).json()
    assert marked == {"marked_missed": 1}, "the push must actually be counted missed"

    pin_clock(monkeypatch, 21, 30)
    body = (await client.post("/internal/trigger-review", headers=CRON_HEADERS)).json()

    assert body["reason"] == "daily_limit"
    assert capture_push == []


async def test_a_malformed_window_is_skipped_rather_than_raising(
    client, db, monkeypatch, capture_push
):
    """`PUT /settings` validates times now, but rows predating that rule exist.

    One bad string must not take every poll down with a 500.
    """
    from app.models import DEFAULT_WINDOWS, Settings

    settings = await db.get(Settings, 1)
    # The good window is the shipped default, so this keeps asserting what it
    # means to assert if those times ever move.
    settings.windows = [
        {"label": "Broken", "from": "25:99", "to": "nonsense", "on": True},
        *DEFAULT_WINDOWS,
    ]
    db.add(settings)
    db.add(make_card(next_review_at=local_today() - timedelta(days=3)))
    await db.commit()

    pin_clock(monkeypatch, 7, 30)
    body = (await client.post("/internal/trigger-review", headers=CRON_HEADERS)).json()

    assert body["sent"] is True


async def test_a_wholly_unparseable_settings_row_fails_loudly(client, db, monkeypatch):
    """The one case where silence would be worse than a red workflow run.

    `outside_window` is the normal answer ~85 times a day, so if every window were
    skipped the product would degrade to "no push ever arrives again" with nothing
    to notice it by. A 500 fails the job, which is the only breakage signal the
    polled workflow still has.
    """
    from app.models import Settings

    settings = await db.get(Settings, 1)
    settings.windows = [{"label": "Broken", "from": "25:99", "to": "nonsense", "on": True}]
    db.add(settings)
    await db.commit()

    pin_clock(monkeypatch, 7, 30)
    assert (await client.post("/internal/trigger-review", headers=CRON_HEADERS)).status_code == 500


async def test_an_offset_bearing_legacy_window_does_not_crash_valid_neighbours(
    client, db, monkeypatch, capture_push
):
    from app.models import Settings

    settings = await db.get(Settings, 1)
    settings.windows = [
        {"label": "Legacy offset", "from": "07:10Z", "to": "08:30Z", "on": True},
        {"label": "Morning", "from": "07:10", "to": "08:30", "on": True},
    ]
    db.add(settings)
    db.add(make_card(next_review_at=local_today()))
    await db.commit()

    pin_clock(monkeypatch, 7, 30)
    body = (await client.post("/internal/trigger-review", headers=CRON_HEADERS)).json()

    assert body["sent"] is True
    assert len(capture_push) == 1


async def test_every_window_switched_off_is_quiet_not_an_error(client, db, monkeypatch):
    """Turning pushes off is legitimate, and must not read as a broken row."""
    from app.models import Settings

    settings = await db.get(Settings, 1)
    settings.windows = [{"label": "Morning", "from": "07:10", "to": "08:30", "on": False}]
    db.add(settings)
    await db.commit()

    pin_clock(monkeypatch, 7, 30)
    body = (await client.post("/internal/trigger-review", headers=CRON_HEADERS)).json()
    assert body["reason"] == "outside_window"


def test_window_guard_resolves_a_spring_forward_gap_to_one_real_start():
    from app.models import Settings

    zone = ZoneInfo("America/Los_Angeles")
    settings = Settings(
        timezone=zone.key,
        windows=[
            {
                "label": "Gap",
                "from": "02:30",
                "to": "03:30",
                "on": True,
                "days": [7],
            }
        ],
    )

    starts = [
        internal._active_window_start(
            settings, datetime(2027, 3, 14, hour, minute, tzinfo=zone)
        )
        for hour, minute in [(3, 0), (3, 15)]
    ]

    assert starts == [datetime(2027, 3, 14, 3, 0, tzinfo=zone)] * 2
    assert starts[0].astimezone(UTC) == datetime(2027, 3, 14, 10, 0, tzinfo=UTC)


def test_window_wholly_inside_a_spring_forward_gap_keeps_its_duration():
    from app.models import Settings

    zone = ZoneInfo("America/Los_Angeles")
    settings = Settings(
        timezone=zone.key,
        windows=[
            {
                "label": "Gap",
                "from": "02:10",
                "to": "02:50",
                "on": True,
                "days": [7],
            }
        ],
    )

    assert internal._active_window_start(
        settings, datetime(2027, 3, 14, 3, 20, tzinfo=zone)
    ) == datetime(2027, 3, 14, 3, 0, tzinfo=zone)
    assert (
        internal._active_window_start(
            settings, datetime(2027, 3, 14, 3, 45, tzinfo=zone)
        )
        is None
    )


def test_window_guard_uses_the_first_start_during_a_fall_back_fold():
    from app.models import Settings

    zone = ZoneInfo("America/Los_Angeles")
    settings = Settings(
        timezone=zone.key,
        windows=[
            {
                "label": "Fold",
                "from": "01:00",
                "to": "01:45",
                "on": True,
                "days": [7],
            }
        ],
    )

    first = internal._active_window_start(
        settings, datetime(2027, 11, 7, 1, 15, tzinfo=zone, fold=0)
    )
    second = internal._active_window_start(
        settings, datetime(2027, 11, 7, 1, 15, tzinfo=zone, fold=1)
    )

    assert first == second
    assert first.astimezone(UTC) == datetime(2027, 11, 7, 8, 0, tzinfo=UTC)


@pytest.mark.parametrize("scheduled_today", [True, False])
async def test_trigger_review_obeys_window_weekdays(
    client, db, monkeypatch, capture_push, scheduled_today
):
    from app.models import Settings

    today = local_today().isoweekday()
    another_day = today % 7 + 1
    settings = await db.get(Settings, 1)
    settings.windows = [
        {
            "label": "Morning",
            "from": "07:10",
            "to": "08:30",
            "on": True,
            "days": [today if scheduled_today else another_day],
        }
    ]
    db.add(settings)
    db.add(make_card(next_review_at=local_today()))
    await db.commit()

    pin_clock(monkeypatch, 7, 30)
    body = (await client.post("/internal/trigger-review", headers=CRON_HEADERS)).json()

    assert body["sent"] is scheduled_today
    assert body["reason"] == (None if scheduled_today else "outside_window")
    assert len(capture_push) == int(scheduled_today)


async def test_trigger_review_treats_a_legacy_window_without_days_as_daily(
    client, db, monkeypatch, capture_push
):
    from app.models import Settings

    settings = await db.get(Settings, 1)
    settings.windows = [{"label": "Morning", "from": "07:10", "to": "08:30", "on": True}]
    db.add(settings)
    db.add(make_card(next_review_at=local_today()))
    await db.commit()

    pin_clock(monkeypatch, 7, 30)
    body = (await client.post("/internal/trigger-review", headers=CRON_HEADERS)).json()

    assert body["sent"] is True
    assert len(capture_push) == 1


async def test_trigger_review_no_ops_outside_the_window(client, db, monkeypatch):
    pin_clock(monkeypatch, 3, 0)
    db.add(make_card(next_review_at=local_today()))
    await db.commit()

    body = (await client.post("/internal/trigger-review", headers=CRON_HEADERS)).json()
    assert body == {"sent": False, "reason": "outside_window", "card_id": None, "due_count": None}


async def test_trigger_review_no_ops_when_nothing_due(client, db, in_window):
    db.add(make_card(next_review_at=local_today() + timedelta(days=5)))
    await db.commit()

    body = (await client.post("/internal/trigger-review", headers=CRON_HEADERS)).json()
    assert body["reason"] == "nothing_due"


async def test_trigger_review_never_calls_claude(client, db, in_window, monkeypatch, capture_push):
    """Generating a question for a push that may never be opened wastes tokens."""

    async def _fail(**_kwargs):
        raise AssertionError("trigger-review must not call the LLM")

    monkeypatch.setattr(llm, "generate_question", _fail)
    monkeypatch.setattr(llm, "score_answer", _fail)

    card = make_card(next_review_at=local_today() - timedelta(days=3))
    db.add(card)
    await db.commit()

    body = (await client.post("/internal/trigger-review", headers=CRON_HEADERS)).json()
    assert body["sent"] is True
    assert body["due_count"] == 1
    assert capture_push[0]["title"] == "1 due"
    assert capture_push[0]["body"] == "Consistent hashing"


async def test_undelivered_push_does_not_mark_the_card_as_pushed(
    client, db, in_window, monkeypatch
):
    """A failed delivery must not later be counted against the user.

    last_pushed_at is what check-missed reads to decide a review was ignored.
    Setting it when APNs reached nobody would inflate missed_count with our own
    delivery failures — and missed_count is a signal about the user.
    """

    async def _push_reaching_nobody(**_kwargs):
        return PushDelivery(sent=0, attempted=0)

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


async def test_check_missed_keeps_the_record_that_a_push_went_out(client, db):
    """`last_pushed_at` is trigger-review's evidence, not check-missed's scratch space.

    Clearing it handed the day's budget back and would re-open a window that had
    already been satisfied. The count is recorded on its own column instead.
    """
    from datetime import UTC, datetime

    pushed_at = datetime.now(UTC) - timedelta(hours=6)
    card = make_card(last_pushed_at=pushed_at)
    db.add(card)
    await db.commit()

    await client.post("/internal/check-missed", headers=CRON_HEADERS)

    await db.refresh(card)
    assert card.last_pushed_at is not None
    assert card.missed_counted_at == card.last_pushed_at


async def test_check_missed_does_not_count_the_same_push_twice(client, db):
    """It runs every four hours; an unanswered push must not accrue a miss each time."""
    from datetime import UTC, datetime

    card = make_card(last_pushed_at=datetime.now(UTC) - timedelta(hours=6))
    db.add(card)
    await db.commit()

    first = (await client.post("/internal/check-missed", headers=CRON_HEADERS)).json()
    second = (await client.post("/internal/check-missed", headers=CRON_HEADERS)).json()

    assert first == {"marked_missed": 1}
    assert second == {"marked_missed": 0}
    await db.refresh(card)
    assert card.missed_count == 1


async def test_a_later_push_becomes_missable_again(client, db):
    """The stamp gates one push, not the card forever."""
    from datetime import UTC, datetime

    card = make_card(last_pushed_at=datetime.now(UTC) - timedelta(hours=9))
    db.add(card)
    await db.commit()
    await client.post("/internal/check-missed", headers=CRON_HEADERS)

    card.last_pushed_at = datetime.now(UTC) - timedelta(hours=5)
    db.add(card)
    await db.commit()

    body = (await client.post("/internal/check-missed", headers=CRON_HEADERS)).json()
    assert body == {"marked_missed": 1}
    await db.refresh(card)
    assert card.missed_count == 2


# --- overview ---------------------------------------------------------------


async def test_overview_counts_every_tier_and_splits_shaky_from_cold(client, db):
    """`counts` covers the whole library; the two lists carry only what needs work.

    `cold` is the lapse case — solid, then left far past its interval — and it
    must not also appear under `shaky`. These are the /cards/overview tiers,
    which fold in ease factor and lapse timing; they share names with Coverage's
    tiers but not their definitions.
    """
    today = local_today()
    db.add(make_card(topic="Untested", repetitions=0))
    db.add(make_card(topic="Shaky", repetitions=2, last_score=2))
    db.add(make_card(topic="Developing", repetitions=2, last_score=3))
    db.add(make_card(topic="Solid", repetitions=4, ease_factor=2.6, last_score=5))
    db.add(
        make_card(
            topic="Lapsed",
            repetitions=4,
            ease_factor=2.6,
            last_score=5,
            interval_days=10,
            next_review_at=today - timedelta(days=30),
        )
    )
    await db.commit()

    body = (await client.get("/cards/overview", headers=API_HEADERS)).json()

    assert body["counts"] == {
        "untested": 1,
        "shaky": 1,
        "developing": 1,
        "solid": 1,
        "cold": 1,
    }
    assert [c["topic"] for c in body["shaky"]] == ["Shaky"]
    assert [c["topic"] for c in body["cold"]] == ["Lapsed"]
    assert body["cold"][0]["days_overdue"] == 30
    # last_score is a shaky-list concern; the cold list answers "how long ago".
    assert body["shaky"][0]["last_score"] == 2
    assert body["cold"][0]["last_score"] is None


async def test_overview_mode_filters_desk_cards(client, db):
    db.add(make_card(topic="Consistent hashing"))
    db.add(make_card(topic="Multi-source BFS", delivery_mode="desk"))
    await db.commit()

    conversational = (
        await client.get("/cards/overview?mode=conversational", headers=API_HEADERS)
    ).json()
    desk = (await client.get("/cards/overview?mode=desk", headers=API_HEADERS)).json()
    every = (await client.get("/cards/overview", headers=API_HEADERS)).json()

    assert sum(conversational["counts"].values()) == 1
    assert sum(desk["counts"].values()) == 1
    assert sum(every["counts"].values()) == 2


# --- settings ---------------------------------------------------------------


async def test_settings_round_trip(client):
    """PUT returns the stored row, and GET agrees with it.

    The window's `from` is a Python keyword aliased to `from_` in the schema —
    this pins the wire format, which is the part a client can actually see.
    """
    payload = {
        "reviews_per_day": 3,
        "timezone": "America/New_York",
        "windows": [
            {
                "label": "Morning",
                "on": True,
                "from": "06:30",
                "to": "09:00",
                "days": [1, 3, 5],
            },
            {
                "label": "Evening",
                "on": False,
                "from": "20:00",
                "to": "22:00",
                "days": [2, 4],
            },
        ],
    }

    put = await client.put("/settings", headers=API_HEADERS, json=payload)
    assert put.status_code == 200
    expected = {**payload, "active_scoring_contract_version": 1}
    assert put.json() == expected
    assert (await client.get("/settings", headers=API_HEADERS)).json() == expected


async def test_settings_normalizes_and_persists_missing_days_as_every_day(client, db):
    from app.models import Settings

    settings = await db.get(Settings, 1)
    settings.windows = [{"label": "Legacy", "on": True, "from": "06:30", "to": "09:00"}]
    db.add(settings)
    await db.commit()

    legacy_read = (await client.get("/settings", headers=API_HEADERS)).json()
    assert legacy_read["windows"][0]["days"] == list(range(1, 8))

    payload = {
        "reviews_per_day": 2,
        "timezone": "America/Los_Angeles",
        "windows": [{"label": "Old client", "on": True, "from": "07:00", "to": "08:00"}],
    }
    written = await client.put("/settings", headers=API_HEADERS, json=payload)

    assert written.status_code == 200
    assert written.json()["windows"][0]["days"] == list(range(1, 8))
    await db.refresh(settings)
    assert settings.windows[0]["days"] == list(range(1, 8))


async def test_settings_rejects_out_of_range_reviews_per_day(client):
    payload = (await client.get("/settings", headers=API_HEADERS)).json()
    payload["reviews_per_day"] = 7

    assert (await client.put("/settings", headers=API_HEADERS, json=payload)).status_code == 422


@pytest.mark.parametrize("timezone", ["attacker/not-a-zone", "", "x" * 101])
async def test_settings_rejects_unknown_or_unbounded_timezones(client, timezone):
    payload = (await client.get("/settings", headers=API_HEADERS)).json()
    payload["timezone"] = timezone

    response = await client.put("/settings", headers=API_HEADERS, json=payload)
    assert response.status_code == 422


async def test_settings_bounds_window_count_and_write_fields(client):
    payload = (await client.get("/settings", headers=API_HEADERS)).json()
    window = {"label": "Morning", "on": True, "from": "07:10", "to": "08:30"}

    payload["windows"] = [window] * 9
    assert (await client.put("/settings", headers=API_HEADERS, json=payload)).status_code == 422

    payload["windows"] = [{**window, "label": "x" * 65}]
    assert (await client.put("/settings", headers=API_HEADERS, json=payload)).status_code == 422

    payload["windows"] = [{**window, "label": "   "}]
    assert (await client.put("/settings", headers=API_HEADERS, json=payload)).status_code == 422

    payload["windows"] = [{**window, "from": "07:10:00"}]
    assert (await client.put("/settings", headers=API_HEADERS, json=payload)).status_code == 422


async def test_lazy_settings_default_never_commits_unrelated_dirty_state(db):
    """A scheduler timezone lookup must stay inside its caller's transaction."""
    from sqlalchemy import delete

    from app.models import FOUNDER_USER_ID, Card, Settings
    from app.routers.deps import get_settings_row

    await db.exec(delete(Settings))
    await db.commit()
    card = make_card(topic="must roll back")
    db.add(card)
    await db.flush()

    default = await get_settings_row(db, FOUNDER_USER_ID)
    assert default.timezone == "America/Los_Angeles"
    await db.rollback()

    assert await db.get(Card, card.id) is None
    assert (await db.exec(select(Settings))).all() == []


@pytest.mark.parametrize("days", [[0], [8], [1, 1], [], ["1"], [True]])
async def test_settings_rejects_invalid_weekday_recurrence(client, days):
    payload = (await client.get("/settings", headers=API_HEADERS)).json()
    payload["windows"] = [
        {"label": "Morning", "on": True, "from": "07:10", "to": "08:30", "days": days}
    ]

    response = await client.put("/settings", headers=API_HEADERS, json=payload)

    assert response.status_code == 422


async def test_settings_rejects_empty_weekdays_on_a_disabled_window(client):
    payload = (await client.get("/settings", headers=API_HEADERS)).json()
    payload["windows"] = [
        {"label": "Morning", "on": False, "from": "07:10", "to": "08:30", "days": []}
    ]

    response = await client.put("/settings", headers=API_HEADERS, json=payload)

    assert response.status_code == 422


async def test_settings_rejects_equal_starts_on_the_same_selected_day(client):
    payload = (await client.get("/settings", headers=API_HEADERS)).json()
    payload["windows"] = [
        {
            "label": "Morning",
            "on": True,
            "from": "07:10",
            "to": "08:30",
            "days": [1, 3, 5],
        },
        {
            "label": "Second",
            "on": True,
            "from": "07:10",
            "to": "12:15",
            "days": [3],
        },
    ]

    response = await client.put("/settings", headers=API_HEADERS, json=payload)

    assert response.status_code == 422


async def test_settings_allows_equal_starts_on_disjoint_selected_days(client):
    payload = (await client.get("/settings", headers=API_HEADERS)).json()
    payload["windows"] = [
        {
            "label": "Monday",
            "on": True,
            "from": "07:10",
            "to": "08:30",
            "days": [1],
        },
        {
            "label": "Tuesday",
            "on": True,
            "from": "07:10",
            "to": "08:30",
            "days": [2],
        },
    ]

    response = await client.put("/settings", headers=API_HEADERS, json=payload)

    assert response.status_code == 200


@pytest.mark.parametrize(
    "from_,to,why",
    [
        ("07:10", "07:25", "shorter than the poll interval"),
        ("08:30", "07:10", "backwards, which reads as a negative span"),
        ("25:99", "26:00", "not a time at all"),
    ],
)
async def test_settings_rejects_a_window_the_poll_could_miss(client, from_, to, why):
    """Keep the shipped 30-minute floor even though the poll now runs twice as often."""
    payload = (await client.get("/settings", headers=API_HEADERS)).json()
    payload["windows"] = [{"label": "Morning", "on": True, "from": from_, "to": to}]

    response = await client.put("/settings", headers=API_HEADERS, json=payload)
    assert response.status_code == 422, why


async def test_reading_settings_still_works_on_a_row_written_before_the_rule(client, db):
    """Validation constrains writes, not reads.

    `read_settings` rebuilds the window models from stored JSON. Putting the rule
    on the shared model would make GET fail on rows that predate it — including
    the hand-widened windows the runbook uses to test a push.
    """
    from app.models import Settings

    settings = await db.get(Settings, 1)
    settings.windows = [{"label": "Tiny", "from": "07:10", "to": "07:12", "on": True}]
    db.add(settings)
    await db.commit()

    response = await client.get("/settings", headers=API_HEADERS)
    assert response.status_code == 200
    assert response.json()["windows"][0]["to"] == "07:12"


async def test_settings_defaults_are_served_before_any_write(client):
    body = (await client.get("/settings", headers=API_HEADERS)).json()

    assert body["reviews_per_day"] == 2
    assert body["timezone"] == "America/Los_Angeles"
    assert [w["label"] for w in body["windows"]] == ["Morning", "Evening"]
    assert all(w["days"] == list(range(1, 8)) for w in body["windows"])


# --- device tokens ----------------------------------------------------------


async def test_device_token_registration_is_idempotent(client, db):
    for _ in range(3):
        resp = await client.post(
            "/device-tokens", headers=API_HEADERS, json={"token": "abc123", "kind": "apns"}
        )
        assert resp.status_code == 204

    tokens = (await db.exec(select(DeviceToken))).all()
    assert len(tokens) == 1
    assert tokens[0].kind == "apns"


async def test_device_token_reregistration_updates_kind(client, db):
    """The same device moving sandbox -> production must not keep the stale kind.

    A TestFlight build re-registers the token it already had; if `kind` stayed
    put, the row would describe the previous build.
    """
    await client.post(
        "/device-tokens", headers=API_HEADERS, json={"token": "abc123", "kind": "apns-sandbox"}
    )
    created = (await db.exec(select(DeviceToken))).one().created_at

    await client.post(
        "/device-tokens", headers=API_HEADERS, json={"token": "abc123", "kind": "apns"}
    )

    row = (await db.exec(select(DeviceToken))).one()
    assert row.kind == "apns"
    assert row.created_at == created  # first-seen, not last-seen


@pytest.mark.parametrize(
    "payload",
    [
        {"token": "   ", "kind": "apns"},
        {"token": "x" * 257, "kind": "apns"},
        {"token": "abc123", "kind": "fcm"},
    ],
)
async def test_device_token_registration_bounds_the_wire_contract(client, payload):
    response = await client.post("/device-tokens", headers=API_HEADERS, json=payload)
    assert response.status_code == 422


async def test_device_token_registration_enforces_a_per_account_cap(client, db):
    from app.routers.devices import MAX_DEVICE_TOKENS_PER_USER

    for index in range(MAX_DEVICE_TOKENS_PER_USER):
        response = await client.post(
            "/device-tokens",
            headers=API_HEADERS,
            json={"token": f"token-{index}", "kind": "apns"},
        )
        assert response.status_code == 204

    # Re-registering an owned token remains idempotent at the limit.
    replay = await client.post(
        "/device-tokens",
        headers=API_HEADERS,
        json={"token": "token-0", "kind": "apns-sandbox"},
    )
    assert replay.status_code == 204

    overflow = await client.post(
        "/device-tokens",
        headers=API_HEADERS,
        json={"token": "one-too-many", "kind": "apns"},
    )
    assert overflow.status_code == 409
    assert overflow.json()["detail"] == {
        "code": "device_token_limit",
        "limit": MAX_DEVICE_TOKENS_PER_USER,
    }
    assert len((await db.exec(select(DeviceToken))).all()) == MAX_DEVICE_TOKENS_PER_USER


# --- capture boundary -------------------------------------------------------


async def test_direct_card_creation_is_closed(client):
    resp = await client.post("/cards", headers=API_HEADERS, json={"topic": "Raft"})

    assert resp.status_code == 409
    assert resp.json()["detail"] == "direct card creation requires grounding; use /captures"


# --- health -----------------------------------------------------------------


async def test_health_needs_no_api_key_and_checks_the_database(client):
    """The Railway healthcheck hits this unauthenticated, so it must stay open.

    It runs a real `SELECT 1` against its own session factory rather than the
    request-scoped dependency — that is the point, since a healthcheck that
    can't reach Postgres should not report ok.
    """
    resp = await client.get("/health")

    assert resp.status_code == 200
    assert resp.json() == {
        "status": "ok",
        "ai_consent_required_policy_version": "anthropic-2026-08-12-v1",
        "ai_consent_latest_supported_policy_version": (
            "anthropic-openai-2026-08-13-v2"
        ),
        "ai_consent_minimum_ios_build": 7,
        "ai_consent_enforcement_enabled": False,
    }


# --- coached re-attempt (turn 3) --------------------------------------------


@pytest.fixture
def stub_reattempt(monkeypatch):
    """Stub turn 3's scorer. Separate call, separate stub — as in the app."""
    calls: list[dict] = []

    async def _reattempt(**kwargs) -> ReattemptResult:
        calls.append(kwargs)
        return _reattempt.result

    _reattempt.result = ReattemptResult(accuracy=4, mastery_summary="reconstructed it")
    monkeypatch.setattr(llm, "score_reattempt", _reattempt)
    return _reattempt, calls


async def _failed_session(client, db, stub, mechanism=1):
    """Run a session to completion with a failing mechanism score."""
    stub.result = completed(mechanism, feedback="Ring position decides ownership.")
    card = make_card(repetitions=3, interval_days=10, ease_factor=2.5)
    db.add(card)
    await db.commit()
    start = (await client.post(f"/cards/{card.id}/sessions", headers=API_HEADERS)).json()
    body = (
        await client.post(
            f"/sessions/{start['session_id']}/answers", headers=API_HEADERS, json={"text": "wrong"}
        )
    ).json()
    return card, uuid.UUID(start["session_id"]), body


async def test_reattempt_never_touches_sm2_or_the_score(client, db, stub_llm, stub_reattempt):
    """The whole feature's guarantee: turn 3 is barred from the scheduler.

    A post-correction turn measures coached performance. If it reached `quality_for`
    it would inflate the interval by the ease factor on exactly the cards just
    gotten wrong — so this asserts the full write set, not just the outcome.
    """
    score, _ = stub_llm
    card, session_id, _ = await _failed_session(client, db, score)
    await db.refresh(card)
    before = (card.ease_factor, card.interval_days, card.repetitions, card.next_review_at)

    resp = await client.post(
        f"/sessions/{session_id}/reattempt", headers=API_HEADERS, json={"text": "it owns the arc"}
    )
    assert resp.status_code == 200

    await db.refresh(card)
    assert (card.ease_factor, card.interval_days, card.repetitions, card.next_review_at) == before
    # The unaided attempt still describes the card: a 4 on the re-attempt must not
    # relabel a card the engineer could not reconstruct on its own.
    assert card.last_score == 1
    assert card.last_accuracy == 1

    session = await db.get(Session, session_id)
    await db.refresh(session)
    assert session.score == 1
    assert session.accuracy == 1
    assert session.reattempt_accuracy == 4


async def test_reattempt_writes_mastery_and_the_transcript(client, db, stub_llm, stub_reattempt):
    score, _ = stub_llm
    reattempt, calls = stub_reattempt
    card, session_id, _ = await _failed_session(client, db, score)

    body = (
        await client.post(
            f"/sessions/{session_id}/reattempt", headers=API_HEADERS, json={"text": "in my words"}
        )
    ).json()

    assert body == {"mastery_summary": "reconstructed it"}
    await db.refresh(card)
    assert card.mastery_summary == "reconstructed it"

    session = await db.get(Session, session_id)
    await db.refresh(session)
    assert session.reattempt_answer == "in my words"
    # Turn 3 re-asks the card's own question rather than generating a new one, so
    # nothing about the prompt is stored — it is a fixed preface plus this column.
    assert calls[0]["question_asked"] == session.question_asked
    # Without the feedback text the model cannot tell reconstruction from recitation.
    assert calls[0]["feedback_given"] == "Ring position decides ownership."
    # And without the unaided score it cannot tell this was a coached turn at all,
    # so it writes summaries that read as unaided mastery.
    assert calls[0]["unaided_accuracy"] == 1


async def test_exact_reattempt_replay_returns_the_committed_result_without_rescoring(
    client, db, stub_llm, stub_reattempt
):
    score, _ = stub_llm
    _, calls = stub_reattempt
    _, session_id, _ = await _failed_session(client, db, score)

    body = {"text": "the arc, not the name"}
    first = await client.post(f"/sessions/{session_id}/reattempt", headers=API_HEADERS, json=body)
    second = await client.post(f"/sessions/{session_id}/reattempt", headers=API_HEADERS, json=body)
    different = await client.post(
        f"/sessions/{session_id}/reattempt",
        headers=API_HEADERS,
        json={"text": "different coached evidence"},
    )

    assert first.status_code == second.status_code == 200
    assert second.json() == first.json()
    assert different.status_code == 409
    assert len(calls) == 1


@pytest.mark.parametrize("mechanism", [3, 4, 5])
async def test_reattempt_rejected_when_the_mechanism_passed(
    client, db, stub_llm, stub_reattempt, mechanism
):
    """Above the band there is no correction to re-attempt — the rubric gave none."""
    score, _ = stub_llm
    _, session_id, body = await _failed_session(client, db, score, mechanism=mechanism)

    assert body["reattempt_offered"] is False
    resp = await client.post(
        f"/sessions/{session_id}/reattempt", headers=API_HEADERS, json={"text": "x"}
    )
    assert resp.status_code == 409


@pytest.mark.parametrize("mechanism", [0, 1, 2])
async def test_complete_offers_a_reattempt_inside_the_band(
    client, db, stub_llm, stub_reattempt, mechanism
):
    score, _ = stub_llm
    _, _, body = await _failed_session(client, db, score, mechanism=mechanism)
    assert body["reattempt_offered"] is True
    assert body["reattempt_prompt"].startswith("In your words: ")


async def test_reattempt_rejected_before_the_session_completes(
    client, db, stub_llm, stub_reattempt
):
    """No score means no correction was ever stated, so there is nothing to say back."""
    card = make_card()
    db.add(card)
    await db.commit()
    start = (await client.post(f"/cards/{card.id}/sessions", headers=API_HEADERS)).json()

    resp = await client.post(
        f"/sessions/{start['session_id']}/reattempt", headers=API_HEADERS, json={"text": "early"}
    )
    assert resp.status_code == 409


async def test_reattempt_failure_leaves_the_row_untouched(client, db, stub_llm, monkeypatch):
    """Scored before anything is written — a failed call loses nothing."""
    score, _ = stub_llm
    _, session_id, _ = await _failed_session(client, db, score)

    async def _boom(**kwargs):
        raise LLMError("upstream is down")

    monkeypatch.setattr(llm, "score_reattempt", _boom)
    resp = await client.post(
        f"/sessions/{session_id}/reattempt", headers=API_HEADERS, json={"text": "lost"}
    )
    assert resp.status_code == 503

    session = await db.get(Session, session_id)
    await db.refresh(session)
    assert session.reattempt_used is False
    assert session.reattempt_answer == ""


async def test_reattempt_rejects_empty_text(client, db, stub_llm, stub_reattempt):
    """An empty body would spend the one re-attempt and rewrite mastery on a 0."""
    score, _ = stub_llm
    _, session_id, _ = await _failed_session(client, db, score)

    resp = await client.post(f"/sessions/{session_id}/reattempt", headers=API_HEADERS, json={})

    assert resp.status_code == 422
    session = await db.get(Session, session_id)
    await db.refresh(session)
    assert session.reattempt_used is False


async def test_reattempt_expires_once_the_card_is_reviewed_again(
    client, db, stub_llm, stub_reattempt
):
    """A stale session's coaching must not overwrite a newer review's summary.

    `mastery_summary` is live context for the next `score_answer`, so this is the
    one indirect path by which turn 3 could reach a future scheduling decision.
    """
    score, _ = stub_llm
    card, session_id, _ = await _failed_session(client, db, score)

    # A second, later session on the same card scores well and rewrites mastery.
    score.result = completed(5, 5, 5, mastery_summary="solid, unaided")
    later = (await client.post(f"/cards/{card.id}/sessions", headers=API_HEADERS)).json()
    await client.post(
        f"/sessions/{later['session_id']}/answers", headers=API_HEADERS, json={"text": "good"}
    )

    resp = await client.post(
        f"/sessions/{session_id}/reattempt", headers=API_HEADERS, json={"text": "late"}
    )

    assert resp.status_code == 409
    await db.refresh(card)
    assert card.mastery_summary == "solid, unaided"


async def test_the_minimum_window_is_at_least_the_poll_interval():
    """The shortest accepted window still gets at least one poll opportunity.

    `MIN_WINDOW_MINUTES` exists because a window shorter than the gap between two
    polls can never be landed in.
    """
    from app.schemas import MIN_WINDOW_MINUTES
    from app.services.review_poller import DEFAULT_POLL_INTERVAL_SECONDS

    poll_interval_minutes = DEFAULT_POLL_INTERVAL_SECONDS // 60
    assert poll_interval_minutes <= MIN_WINDOW_MINUTES, (
        f"poller leaves {poll_interval_minutes} minutes between polls, but windows as short as "
        f"{MIN_WINDOW_MINUTES} minutes are accepted"
    )
