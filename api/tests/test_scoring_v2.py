"""End-to-end invariants for the dark-launched Recall-only contract."""

import uuid
from datetime import UTC, datetime

from app.config import get_settings
from app.models import Card, PendingCapture, Session
from app.services import llm
from app.services.llm import CoachingResult, ScoreResult
from tests.conftest import API_HEADERS, local_today, make_card


def v2_result(recall: int, *, status: str = "complete", probe: str | None = None):
    return ScoreResult(
        status=status,
        score=recall if status == "complete" else None,
        accuracy=recall if status == "complete" else None,
        feedback="The essential account is grounded.",
        follow_up_question=probe,
        mastery_summary="recalled the essential account",
        scoring_contract_version=2,
    )


async def install_v2(monkeypatch, result: ScoreResult):
    monkeypatch.setattr(get_settings(), "scoring_contract_version", 2)
    calls = []

    async def score_answer(**kwargs):
        calls.append(kwargs)
        return result

    monkeypatch.setattr(llm, "score_answer", score_answer)
    return calls


async def complete(client, card_id: uuid.UUID, text: str = "answer"):
    started = (await client.post(f"/cards/{card_id}/sessions", headers=API_HEADERS)).json()
    response = await client.post(
        f"/sessions/{started['session_id']}/answers",
        headers=API_HEADERS,
        json={"text": text},
    )
    return started, response


async def test_v2_completion_writes_recall_only_and_preserves_scheduler_mapping(
    client, db, monkeypatch
):
    calls = await install_v2(monkeypatch, v2_result(4))
    card = make_card(
        canonical_question="What is the essential account?",
        repetitions=1,
        interval_days=1,
        last_depth=5,
        last_boundaries=5,
    )
    db.add(card)
    await db.commit()

    started, response = await complete(client, card.id)
    assert response.status_code == 200
    body = response.json()
    assert body["score"] == body["recall_score"] == 4
    assert body["scoring_contract_version"] == 2
    assert body["coaching_offered"] is True
    assert body["coaching_focus"] == "depth"
    assert body["reattempt_offered"] is False
    assert calls[0]["scoring_contract_version"] == 2

    await db.refresh(card)
    session = await db.get(Session, uuid.UUID(started["session_id"]))
    assert (session.score, session.accuracy) == (4, 4)
    assert session.depth is None
    assert session.boundaries is None
    assert session.scoring_contract_version == 2
    assert card.last_score == card.last_accuracy == 4
    assert card.last_depth is None
    assert card.last_boundaries is None
    assert card.last_score_contract_version == 2
    # Same `good` bucket the V1 Accuracy 4 path uses.
    assert (card.repetitions, card.interval_days) == (2, 6)


async def test_v2_failed_recall_offers_reattempt_not_qualitative_coaching(
    client, db, monkeypatch
):
    await install_v2(monkeypatch, v2_result(2))
    card = make_card(canonical_question="What is the essential account?")
    db.add(card)
    await db.commit()

    started, response = await complete(client, card.id)
    body = response.json()
    assert body["reattempt_offered"] is True
    assert body["coaching_offered"] is False
    assert body["coaching_focus"] is None
    assert (
        await client.post(
            f"/sessions/{started['session_id']}/coaching",
            headers=API_HEADERS,
            json={"text": "deeper answer"},
        )
    ).status_code == 409


async def test_v2_exact_initial_answer_replay_is_not_scored_as_the_follow_up(
    client, db, monkeypatch
):
    calls = await install_v2(
        monkeypatch,
        v2_result(2, status="follow_up", probe="One more — name the missing link?"),
    )
    card = make_card(canonical_question="What is the essential account?")
    db.add(card)
    await db.commit()

    started = (await client.post(f"/cards/{card.id}/sessions", headers=API_HEADERS)).json()
    endpoint = f"/sessions/{started['session_id']}/answers"
    payload = {"text": "the same partial recall"}
    first = await client.post(endpoint, headers=API_HEADERS, json=payload)
    replay = await client.post(endpoint, headers=API_HEADERS, json=payload)

    assert first.json() == replay.json() == {
        "status": "follow_up",
        "question": "One more — name the missing link?",
    }
    assert len(calls) == 1
    session = await db.get(Session, uuid.UUID(started["session_id"]))
    await db.refresh(session)
    assert session.status == "awaiting_follow_up"
    assert session.scoring_contract_version == 2
    assert session.follow_up_answer == ""
    assert session.score is None


async def test_qualitative_coaching_changes_only_its_four_session_fields(
    client, db, monkeypatch
):
    await install_v2(monkeypatch, v2_result(4))
    card = make_card(canonical_question="What is the essential account?")
    db.add(card)
    await db.commit()
    started, _ = await complete(client, card.id)
    session_id = uuid.UUID(started["session_id"])
    session = await db.get(Session, session_id)
    await db.refresh(card)
    card_before = card.model_dump()
    session_before = session.model_dump()
    calls = 0

    async def coach_answer(**_kwargs):
        nonlocal calls
        calls += 1
        return CoachingResult(feedback="The causal link is grounded.")

    monkeypatch.setattr(llm, "coach_answer", coach_answer)
    response = await client.post(
        f"/sessions/{session_id}/coaching",
        headers=API_HEADERS,
        json={"text": "It explains the causal link."},
    )
    assert response.status_code == 200
    assert response.json()["focus"] == "depth"

    await db.refresh(card)
    await db.refresh(session)
    assert card.model_dump() == card_before
    changed = {
        key
        for key, before in session_before.items()
        if getattr(session, key) != before
    }
    assert changed == {
        "coaching_focus",
        "coaching_question",
        "coaching_answer",
        "coaching_feedback",
    }

    replay = await client.post(
        f"/sessions/{session_id}/coaching",
        headers=API_HEADERS,
        json={"text": "again"},
    )
    assert replay.status_code == 409
    assert calls == 1


async def test_completed_coaching_alternates_the_next_card_focus(
    client, db, monkeypatch
):
    await install_v2(monkeypatch, v2_result(5))
    card = make_card(canonical_question="What is the essential account?")
    db.add(card)
    await db.commit()

    first_started, _ = await complete(client, card.id)
    first = await db.get(Session, uuid.UUID(first_started["session_id"]))
    first.coaching_focus = "depth"
    first.coaching_question = "Depth question"
    first.coaching_answer = "Depth answer"
    first.coaching_feedback = "Depth feedback"
    db.add(first)
    await db.commit()

    second_started, second_response = await complete(client, card.id)
    assert second_response.json()["coaching_focus"] == "boundaries"

    async def coach_answer(**_kwargs):
        return CoachingResult(feedback="Boundary feedback")

    monkeypatch.setattr(llm, "coach_answer", coach_answer)
    coached = await client.post(
        f"/sessions/{second_started['session_id']}/coaching",
        headers=API_HEADERS,
        json={"text": "A boundary answer"},
    )
    assert coached.json()["focus"] == "boundaries"


async def test_v2_practice_writes_recall_but_leaves_all_sm2_fields_unchanged(
    client, db, monkeypatch
):
    await install_v2(monkeypatch, v2_result(3))
    card = make_card(
        canonical_question="What is the essential account?",
        ease_factor=2.36,
        interval_days=6,
        repetitions=3,
        next_review_at=local_today(),
    )
    db.add(card)
    await db.commit()
    protected = (
        card.ease_factor,
        card.interval_days,
        card.repetitions,
        card.next_review_at,
    )

    started = (
        await client.post(
            f"/cards/{card.id}/sessions?practice=true", headers=API_HEADERS
        )
    ).json()
    response = await client.post(
        f"/sessions/{started['session_id']}/answers",
        headers=API_HEADERS,
        json={"text": "answer"},
    )
    assert response.json()["practice"] is True
    await db.refresh(card)
    assert (
        card.ease_factor,
        card.interval_days,
        card.repetitions,
        card.next_review_at,
    ) == protected


async def test_stale_qualitative_offer_cannot_overwrite_a_newer_review(
    client, db, monkeypatch
):
    await install_v2(monkeypatch, v2_result(4))
    card = make_card(canonical_question="What is the essential account?")
    db.add(card)
    await db.commit()
    started, _ = await complete(client, card.id)
    session = await db.get(Session, uuid.UUID(started["session_id"]))
    card.last_reviewed_at = datetime.now(UTC)
    session.ended_at = datetime(2026, 1, 1, tzinfo=UTC)
    db.add(card)
    db.add(session)
    await db.commit()

    response = await client.post(
        f"/sessions/{session.id}/coaching",
        headers=API_HEADERS,
        json={"text": "late answer"},
    )
    assert response.status_code == 409


async def test_v2_scoring_failure_leaves_session_card_and_schedule_untouched(
    client, db, monkeypatch
):
    monkeypatch.setattr(get_settings(), "scoring_contract_version", 2)
    card = make_card(
        canonical_question="What is the essential account?",
        mastery_summary="prior recall signal",
        ease_factor=2.36,
        interval_days=6,
        repetitions=3,
    )
    db.add(card)
    await db.commit()
    card_before = card.model_dump()
    started = (
        await client.post(f"/cards/{card.id}/sessions", headers=API_HEADERS)
    ).json()
    session = await db.get(Session, uuid.UUID(started["session_id"]))
    session_before = session.model_dump()

    async def fail(**_kwargs):
        raise llm.LLMError("invalid V2 response")

    monkeypatch.setattr(llm, "score_answer", fail)
    response = await client.post(
        f"/sessions/{session.id}/answers",
        headers=API_HEADERS,
        json={"text": "answer retained by the client"},
    )
    assert response.status_code == 503
    await db.refresh(card)
    await db.refresh(session)

    def without_timezone(value):
        return value.replace(tzinfo=None) if isinstance(value, datetime) else value

    assert {key: without_timezone(value) for key, value in card.model_dump().items()} == {
        key: without_timezone(value) for key, value in card_before.items()
    }
    assert {
        key: without_timezone(value) for key, value in session.model_dump().items()
    } == {key: without_timezone(value) for key, value in session_before.items()}


async def test_settings_declares_v2_without_making_the_capability_writable(
    client, monkeypatch
):
    monkeypatch.setattr(get_settings(), "scoring_contract_version", 2)
    current = (await client.get("/settings", headers=API_HEADERS)).json()
    assert current["active_scoring_contract_version"] == 2
    current["active_scoring_contract_version"] = 1
    updated = await client.put("/settings", headers=API_HEADERS, json=current)
    assert updated.json()["active_scoring_contract_version"] == 2


async def test_v2_creation_accepts_both_rubric_vocabularies_and_stores_v2_keys(
    client, db, monkeypatch
):
    monkeypatch.setattr(get_settings(), "scoring_contract_version", 2)
    created = await client.post(
        "/captures",
        headers=API_HEADERS,
        json={"topic": "Subject-agnostic grounding"},
    )
    capture_id = uuid.UUID(created.json()["id"])
    v2_rubric = {
        "essential_account": "The source-supported account.",
        "acceptable_alternative": "An equivalent account.",
        "depth_extension": "The causal extension.",
        "boundary_extension": "The limiting condition.",
        "misconception": "A plausible wrong account.",
    }
    updated = await client.patch(
        f"/captures/{capture_id}",
        headers=API_HEADERS,
        json={
            "source_label": "Trusted source",
            "answer_basis": "Trusted answer basis",
            "answer_rubric": v2_rubric,
            "canonical_question": "What is the essential account?",
        },
    )
    assert updated.status_code == 200
    capture = await db.get(PendingCapture, capture_id)
    assert capture.answer_rubric == v2_rubric

    activated = await client.post(
        f"/captures/{capture_id}/activate",
        headers=API_HEADERS,
        json={"schedule": "now"},
    )
    assert activated.status_code == 201
    card = await db.get(Card, uuid.UUID(activated.json()["id"]))
    assert card.answer_rubric == v2_rubric
