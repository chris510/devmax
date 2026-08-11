"""Compatibility boundaries for the versioned V1/V2 score contract."""

import uuid

from app.models import Session
from app.services import llm
from app.services.llm import ScoreResult
from app.services.scoring_contract import (
    CardScoreProjection,
    SessionScoreProjection,
    project_card_score,
    project_session_score,
)
from tests.conftest import API_HEADERS, make_card


def test_card_projection_exposes_accuracy_as_recall_without_relabeling_composite():
    decomposed = make_card(
        last_score=4,
        last_accuracy=5,
        last_depth=3,
        last_boundaries=2,
        last_score_contract_version=1,
    )
    legacy = make_card(last_score=3)
    unrated = make_card()

    assert project_card_score(decomposed) == CardScoreProjection(5, "recall", 1)
    assert project_card_score(legacy) == CardScoreProjection(None, "legacy_composite", 1)
    assert project_card_score(unrated) == CardScoreProjection(None, "unrated", None)


def test_session_projection_keeps_v1_composite_separate_and_v2_composite_empty():
    v1 = Session(
        card_id=uuid.uuid4(),
        question_asked="What is the rule?",
        score=4,
        accuracy=5,
        depth=3,
        boundaries=2,
    )
    v2 = Session(
        card_id=uuid.uuid4(),
        question_asked="What is the rule?",
        score=5,
        accuracy=5,
        scoring_contract_version=2,
    )

    assert project_session_score(v1) == SessionScoreProjection(5, 4, 1)
    assert project_session_score(v2) == SessionScoreProjection(5, None, 2)


async def test_compatibility_api_distinguishes_decomposed_and_composite_only_history(
    client, db
):
    card = make_card(
        last_score=4,
        last_accuracy=5,
        last_depth=3,
        last_boundaries=2,
        last_score_contract_version=1,
    )
    db.add(card)
    db.add(
        Session(
            card_id=card.id,
            question_asked="Newer question",
            answer_text="Newer answer",
            score=4,
            accuracy=5,
            depth=3,
            boundaries=2,
            coaching_focus="depth",
            coaching_question="Why does it matter?",
            coaching_answer="Because the dependency is causal.",
            coaching_feedback="The causal link is grounded.",
            status="complete",
        )
    )
    db.add(
        Session(
            card_id=card.id,
            question_asked="Legacy question",
            answer_text="Legacy answer",
            score=3,
            accuracy=None,
            status="complete",
        )
    )
    await db.commit()

    due = (await client.get("/cards/due", headers=API_HEADERS)).json()[0]
    assert due["last_score"] == 4
    assert due["recall_score"] == 5
    assert due["score_kind"] == "recall"
    assert due["scoring_contract_version"] == 1

    detail = (await client.get(f"/cards/{card.id}", headers=API_HEADERS)).json()
    assert detail["recall_score"] == 5
    by_question = {row["turns"][0]["text"]: row for row in detail["sessions"]}
    decomposed = by_question["Newer question"]
    composite_only = by_question["Legacy question"]
    assert decomposed["recall_score"] == 5
    assert decomposed["legacy_composite_score"] == 4
    assert decomposed["scoring_contract_version"] == 1
    assert decomposed["coaching_focus"] == "depth"
    assert decomposed["coaching_question"] == "Why does it matter?"
    assert decomposed["coaching_answer"] == "Because the dependency is causal."
    assert decomposed["coaching_feedback"] == "The causal link is grounded."
    assert composite_only["recall_score"] is None
    assert composite_only["legacy_composite_score"] == 3


async def test_stage_one_exposes_v1_capability_and_writes_only_v1_metadata(
    client, db, monkeypatch
):
    result = ScoreResult(
        status="complete",
        score=4,
        accuracy=5,
        depth=3,
        boundaries=2,
        feedback="Good.",
        mastery_summary="ok",
    )

    async def score_answer(**_kwargs):
        return result

    monkeypatch.setattr(llm, "score_answer", score_answer)
    card = make_card(canonical_question="What is the rule?")
    db.add(card)
    await db.commit()

    settings = (await client.get("/settings", headers=API_HEADERS)).json()
    assert settings["active_scoring_contract_version"] == 1
    settings["active_scoring_contract_version"] = 2
    written = (await client.put("/settings", headers=API_HEADERS, json=settings)).json()
    assert written["active_scoring_contract_version"] == 1

    start = (await client.post(f"/cards/{card.id}/sessions", headers=API_HEADERS)).json()
    completed = (
        await client.post(
            f"/sessions/{start['session_id']}/answers",
            headers=API_HEADERS,
            json={"text": "answer"},
        )
    ).json()

    assert completed["scoring_contract_version"] == 1
    assert completed["recall_score"] == result.accuracy
    await db.refresh(card)
    session = await db.get(Session, uuid.UUID(start["session_id"]))
    assert card.last_score_contract_version == 1
    assert session.scoring_contract_version == 1
    assert session.coaching_focus is None
    assert session.coaching_question is None
    assert session.coaching_answer is None
    assert session.coaching_feedback is None
