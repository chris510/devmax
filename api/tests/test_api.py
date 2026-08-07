import uuid
from datetime import timedelta

import pytest
from sqlmodel import select

from app.models import STATUS_AWAITING_FOLLOW_UP, STATUS_COMPLETE, DeviceToken, Session
from app.routers import internal
from app.services import llm
from app.services.llm import LLMError, ReattemptResult, ScoreResult
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
        mechanism_accuracy=mechanism,
        trade_off_awareness=trade_offs,
        failure_mode_awareness=failure_modes,
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

    score.result = completed(3, mastery_summary="s")
    resp = await client.post(
        f"/sessions/{start['session_id']}/answers", headers=API_HEADERS, json={"text": "more"}
    )
    # Second call sees follow_up_used=True, so the scorer cannot probe again.
    assert calls[1]["follow_up_used"] is True
    assert resp.json()["status"] == "complete"


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
    assert card.last_mechanism_accuracy == 4
    assert card.last_trade_off_awareness == 3
    assert card.last_failure_mode_awareness == 0
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


async def test_later_sessions_reuse_the_question_without_calling_the_model(
    client, db, stub_llm
):
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
    assert row["last_mechanism_accuracy"] is None

    start = (await client.post(f"/cards/{card.id}/sessions", headers=API_HEADERS)).json()
    await client.post(
        f"/sessions/{start['session_id']}/answers", headers=API_HEADERS, json={"text": "a"}
    )

    row = (await client.get("/cards", headers=API_HEADERS)).json()[0]
    assert row["days_since_review"] == 0
    assert row["last_mechanism_accuracy"] == 4
    assert row["last_trade_off_awareness"] == 3
    assert row["last_failure_mode_awareness"] == 1


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


@pytest.fixture
def capture_push(monkeypatch):
    """Records every push instead of sending one, and reports it reached a device."""
    sent: list[dict] = []

    async def _push(**kwargs):
        sent.append(kwargs)
        return 1

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


async def test_the_same_card_is_not_offered_twice_in_one_day(
    client, db, monkeypatch, capture_push
):
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

    `outside_window` is the normal answer ~46 times a day, so if every window were
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
            {"label": "Morning", "on": True, "from": "06:30", "to": "09:00"},
            {"label": "Evening", "on": False, "from": "20:00", "to": "22:00"},
        ],
    }

    put = await client.put("/settings", headers=API_HEADERS, json=payload)
    assert put.status_code == 200
    assert put.json() == payload
    assert (await client.get("/settings", headers=API_HEADERS)).json() == payload


async def test_settings_rejects_out_of_range_reviews_per_day(client):
    payload = (await client.get("/settings", headers=API_HEADERS)).json()
    payload["reviews_per_day"] = 7

    assert (await client.put("/settings", headers=API_HEADERS, json=payload)).status_code == 422


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
    assert resp.json() == {"status": "ok"}


# --- coached re-attempt (turn 3) --------------------------------------------


@pytest.fixture
def stub_reattempt(monkeypatch):
    """Stub turn 3's scorer. Separate call, separate stub — as in the app."""
    calls: list[dict] = []

    async def _reattempt(**kwargs) -> ReattemptResult:
        calls.append(kwargs)
        return _reattempt.result

    _reattempt.result = ReattemptResult(mechanism_accuracy=4, mastery_summary="reconstructed it")
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
    assert card.last_mechanism_accuracy == 1

    session = await db.get(Session, session_id)
    await db.refresh(session)
    assert session.score == 1
    assert session.mechanism_accuracy == 1
    assert session.reattempt_mechanism_accuracy == 4


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
    assert calls[0]["unaided_mechanism"] == 1


async def test_reattempt_cannot_be_replayed(client, db, stub_llm, stub_reattempt):
    score, _ = stub_llm
    _, session_id, _ = await _failed_session(client, db, score)

    body = {"text": "the arc, not the name"}
    first = await client.post(f"/sessions/{session_id}/reattempt", headers=API_HEADERS, json=body)
    second = await client.post(f"/sessions/{session_id}/reattempt", headers=API_HEADERS, json=body)

    assert first.status_code == 200
    assert second.status_code == 409


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
