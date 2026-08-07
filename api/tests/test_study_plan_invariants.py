"""The safety boundary: Study Plan never touches the review schedule.

`AGENTS.md` lists the fields a Study Plan operation may not modify. This module
snapshots every column of `cards` and `sessions` before and after each Study Plan
operation and asserts byte-equality. It is deliberately paranoid — a regression
here is invisible in every other test, because the plan screens keep working
perfectly while the review schedule quietly drifts.

The one authorised exception is a committed card-proposal acceptance, which
*creates* cards. It is tested here too, for the properties that still hold: it
never modifies an existing card, and a created card starts at SM-2 defaults.
"""

import uuid
from datetime import date, timedelta

import pytest
from sqlmodel import col, delete, select

from app.models import Card, Session, StudyPlanCardProposal
from tests.conftest import API_HEADERS, local_today, make_card
from tests.test_study_plan_api import (  # noqa: F401
    _complete_practice_item,
    _one_suggested,
    make_plan,
)

# Every column on both tables. Listed by name rather than derived, so adding a
# column to `cards` and forgetting it here is a test failure, not a silent gap.
CARD_COLUMNS = (
    "id",
    "user_id",
    "topic",
    "category",
    "pattern",
    "source_company",
    "target_week",
    "delivery_mode",
    "canonical_question",
    "answer_anchor",
    "source_excerpt",
    "source_id",
    "ease_factor",
    "interval_days",
    "repetitions",
    "next_review_at",
    "last_score",
    "last_accuracy",
    "last_depth",
    "last_boundaries",
    "last_reviewed_at",
    "mastery_summary",
    "missed_count",
    "last_pushed_at",
    "missed_counted_at",
    "created_at",
    "updated_at",
)

SESSION_COLUMNS = (
    "id",
    "card_id",
    "question_asked",
    "follow_up_question",
    "answer_text",
    "follow_up_answer",
    "draft_text",
    "score",
    "accuracy",
    "depth",
    "boundaries",
    "feedback",
    "follow_up_used",
    "reattempt_answer",
    "reattempt_accuracy",
    "reattempt_used",
    "practice",
    "status",
    "started_at",
    "ended_at",
)


def test_the_column_lists_above_are_complete():
    """If this fails, a column was added and the snapshot below stopped covering it."""
    assert set(Card.model_fields) == set(CARD_COLUMNS)
    assert set(Session.model_fields) == set(SESSION_COLUMNS)


async def snapshot(db):
    """Every field of every card and session, as plain comparable values."""
    cards = (await db.exec(select(Card).order_by(Card.topic))).all()
    sessions = (await db.exec(select(Session).order_by(Session.started_at))).all()
    return (
        [tuple(getattr(c, name) for name in CARD_COLUMNS) for c in cards],
        [tuple(getattr(s, name) for name in SESSION_COLUMNS) for s in sessions],
    )


@pytest.fixture
async def review_state(db):
    """A card mid-way through a real SM-2 history, plus a completed session."""
    card = make_card(
        topic="Consistent hashing",
        ease_factor=2.36,
        interval_days=6,
        repetitions=3,
        next_review_at=local_today() + timedelta(days=6),
        last_score=4,
        last_accuracy=4,
        last_depth=3,
        last_boundaries=2,
        mastery_summary="solid on ring mechanics, shaky on virtual nodes",
        missed_count=2,
        canonical_question="You add a sixth node to a five-node ring — what moves?",
    )
    db.add(card)
    db.add(
        Session(
            card_id=card.id,
            question_asked="You add a sixth node to a five-node ring — what moves?",
            answer_text="Only the keys between the new node and its predecessor.",
            score=4,
            accuracy=4,
            depth=3,
            boundaries=2,
            feedback="Good on ring mechanics.",
            status="complete",
        )
    )
    await db.commit()
    return card


async def _first_item(client, plan) -> str:
    week = (await client.get(f"/study-plans/{plan['id']}/weeks/1", headers=API_HEADERS)).json()
    return week["sections"][0]["rows"][0]["id"]


# --- one test per operation the safety boundary names ------------------------


async def test_creating_a_plan_touches_no_card_or_session(client, db, review_state, stub_import):
    before = await snapshot(db)
    await make_plan(client)
    assert await snapshot(db) == before


async def test_completing_an_item_touches_no_card_or_session(client, db, review_state, stub_import):
    plan = await make_plan(client)
    item_id = await _first_item(client, plan)
    before = await snapshot(db)

    await client.post(f"/study-plans/{plan['id']}/items/{item_id}/complete", headers=API_HEADERS)
    assert await snapshot(db) == before


async def test_reopening_an_item_preserves_every_card_and_sm2_field(
    client, db, review_state, stub_import
):
    """Reopen is the operation most likely to be mistaken for "undo the review"."""
    plan = await make_plan(client)
    item_id = await _first_item(client, plan)
    await client.post(f"/study-plans/{plan['id']}/items/{item_id}/complete", headers=API_HEADERS)
    before = await snapshot(db)

    overview = (await client.get(f"/study-plans/{plan['id']}", headers=API_HEADERS)).json()
    await client.post(
        f"/study-plans/{plan['id']}/items/{item_id}/reopen/preview", headers=API_HEADERS
    )
    reopened = await client.post(
        f"/study-plans/{plan['id']}/items/{item_id}/reopen",
        params={"base_plan_revision": overview["revision"]},
        headers=API_HEADERS,
    )
    assert reopened.status_code == 200
    assert await snapshot(db) == before

    # And spelled out, because these five are the ones AGENTS.md names.
    card = (await db.exec(select(Card).where(Card.topic == "Consistent hashing"))).one()
    assert card.ease_factor == 2.36
    assert card.interval_days == 6
    assert card.repetitions == 3
    assert card.next_review_at == local_today() + timedelta(days=6)
    assert card.mastery_summary == "solid on ring mechanics, shaky on virtual nodes"


async def test_replanning_touches_no_card_or_session(client, db, review_state, stub_import):
    plan = await make_plan(client)
    before = await snapshot(db)

    await client.post(
        f"/study-plans/{plan['id']}/replans/preview",
        json={"base_plan_revision": plan["revision"]},
        headers=API_HEADERS,
    )
    applied = await client.post(
        f"/study-plans/{plan['id']}/replans/apply",
        json={
            "base_plan_revision": plan["revision"],
            "capacity_overrides": {"1": 30},
        },
        headers=API_HEADERS,
    )
    assert applied.status_code == 200
    assert await snapshot(db) == before


async def test_a_capacity_change_touches_no_card_or_session(client, db, review_state, stub_import):
    plan = await make_plan(client)
    before = await snapshot(db)
    await client.patch(
        f"/study-plans/{plan['id']}/weeks/1/capacity",
        json={
            "week_index": 1,
            "override_capacity_minutes": 600,
            "base_plan_revision": plan["revision"],
        },
        headers=API_HEADERS,
    )
    assert await snapshot(db) == before


@pytest.mark.parametrize("action", ["pause", "complete", "archive"])
async def test_lifecycle_transitions_touch_no_card_or_session(
    client, db, review_state, stub_import, action
):
    plan = await make_plan(client)
    before = await snapshot(db)
    response = await client.post(f"/study-plans/{plan['id']}/{action}", headers=API_HEADERS)
    assert response.status_code == 200
    assert await snapshot(db) == before


async def test_pausing_and_resuming_touches_no_card_or_session(
    client, db, review_state, stub_import
):
    plan = await make_plan(client)
    before = await snapshot(db)

    await client.post(f"/study-plans/{plan['id']}/pause", headers=API_HEADERS)
    overview = (await client.get(f"/study-plans/{plan['id']}", headers=API_HEADERS)).json()
    await client.post(f"/study-plans/{plan['id']}/resume/preview", headers=API_HEADERS)
    resumed = await client.post(
        f"/study-plans/{plan['id']}/resume/apply",
        params={"base_plan_revision": overview["revision"]},
        headers=API_HEADERS,
    )
    assert resumed.status_code == 200
    assert await snapshot(db) == before


async def test_activating_another_plan_touches_no_card_or_session(
    client, db, review_state, stub_import
):
    first = await make_plan(client)
    await client.post(f"/study-plans/{first['id']}/pause", headers=API_HEADERS)
    second = await make_plan(client)
    before = await snapshot(db)

    overview = (await client.get(f"/study-plans/{first['id']}", headers=API_HEADERS)).json()
    activated = await client.post(
        f"/study-plans/{first['id']}/activate",
        params={"base_plan_revision": overview["revision"]},
        headers=API_HEADERS,
    )
    assert activated.status_code == 200
    assert second["id"] != first["id"]
    assert await snapshot(db) == before


async def test_duplicating_copies_no_card_score_session_or_sm2_state(
    client, db, review_state, stub_import, stub_cards
):
    """The duplicate must not carry the original's cards forward.

    Tested by giving the source plan a real, committed card first: a copy that
    duplicated links or cards would show up as a changed snapshot.
    """
    plan = await make_plan(client)
    proposal = await _one_suggested(client, stub_cards, plan)
    await client.post(
        f"/study-plans/{plan['id']}/card-proposals/accept",
        json={
            "selected_proposal_ids": [proposal["id"]],
            "idempotency_key": "invariant-duplicate-01",
            "proposal_revision": proposal["revision"],
        },
        headers=API_HEADERS,
    )
    before = await snapshot(db)

    copy = await client.post(f"/study-plans/{plan['id']}/duplicate", headers=API_HEADERS)
    assert copy.status_code == 201
    assert await snapshot(db) == before

    from app.models import StudyPlanCardLink

    links = (
        await db.exec(
            select(StudyPlanCardLink).where(
                StudyPlanCardLink.plan_id == uuid.UUID(copy.json()["id"])
            )
        )
    ).all()
    assert links == []


async def test_editing_an_item_touches_no_card_or_session(client, db, review_state, stub_import):
    plan = await make_plan(client)
    item_id = await _first_item(client, plan)
    before = await snapshot(db)
    await client.patch(
        f"/study-plans/{plan['id']}/items/{item_id}",
        json={"estimate_minutes": 120, "notes": "hmm"},
        headers=API_HEADERS,
    )
    assert await snapshot(db) == before


async def test_generating_card_proposals_creates_no_cards(
    client, db, review_state, stub_import, stub_cards
):
    """Proposals are proposals. Nothing exists until a confirmed acceptance."""
    plan = await make_plan(client)
    before = await snapshot(db)
    await _one_suggested(client, stub_cards, plan)

    assert await snapshot(db) == before
    assert len((await db.exec(select(StudyPlanCardProposal))).all()) == 1


async def test_saving_and_submitting_a_practice_debrief_touches_no_review_state(
    client, db, review_state, stub_import
):
    plan = await make_plan(client)
    item_id = await _complete_practice_item(client, plan)
    before = await snapshot(db)
    path = f"/study-plans/{plan['id']}/items/{item_id}/practice-debrief"

    await client.patch(f"{path}/draft", json={"text": "Retries felt shaky."}, headers=API_HEADERS)
    submitted = await client.post(path, json={"text": "Retries felt shaky."}, headers=API_HEADERS)
    assert submitted.status_code == 200
    assert await snapshot(db) == before


# --- the one authorised exception -------------------------------------------


async def test_accepting_a_proposal_adds_a_card_and_changes_no_existing_one(
    client, db, review_state, stub_import, stub_cards
):
    plan = await make_plan(client)
    proposal = await _one_suggested(client, stub_cards, plan)
    before_cards, before_sessions = await snapshot(db)

    response = await client.post(
        f"/study-plans/{plan['id']}/card-proposals/accept",
        json={
            "selected_proposal_ids": [proposal["id"]],
            "idempotency_key": "invariant-accept-01",
            "proposal_revision": proposal["revision"],
        },
        headers=API_HEADERS,
    )
    assert response.status_code == 200

    after_cards, after_sessions = await snapshot(db)
    # Exactly one card appeared, no session did, and every pre-existing card row
    # is byte-identical.
    assert len(after_cards) == len(before_cards) + 1
    assert after_sessions == before_sessions
    existing_topics = {row[CARD_COLUMNS.index("topic")] for row in before_cards}
    for row in after_cards:
        if row[CARD_COLUMNS.index("topic")] in existing_topics:
            assert row in before_cards


async def test_a_card_created_from_a_proposal_starts_at_sm2_defaults(
    client, db, review_state, stub_import, stub_cards
):
    plan = await make_plan(client)
    proposal = await _one_suggested(client, stub_cards, plan)
    await client.post(
        f"/study-plans/{plan['id']}/card-proposals/accept",
        json={
            "selected_proposal_ids": [proposal["id"]],
            "idempotency_key": "invariant-accept-02",
            "proposal_revision": proposal["revision"],
        },
        headers=API_HEADERS,
    )
    card = (await db.exec(select(Card).where(Card.topic == "Postgres write path"))).one()
    assert (card.ease_factor, card.interval_days, card.repetitions) == (2.5, 1, 0)
    assert card.last_score is None
    assert card.mastery_summary == ""
    assert card.missed_count == 0
    assert card.next_review_at == local_today()
    assert isinstance(card.next_review_at, date)


async def test_deleting_a_plan_leaves_its_cards_alone(
    client, db, review_state, stub_import, stub_cards
):
    """The card link cascades; the card does not.

    This is the property that makes linkage-in-a-join-table worth the extra
    table — a plan can be deleted outright and the review schedule survives.
    """
    from app.models import StudyPlan, StudyPlanCardLink

    plan = await make_plan(client)
    proposal = await _one_suggested(client, stub_cards, plan)
    await client.post(
        f"/study-plans/{plan['id']}/card-proposals/accept",
        json={
            "selected_proposal_ids": [proposal["id"]],
            "idempotency_key": "invariant-accept-03",
            "proposal_revision": proposal["revision"],
        },
        headers=API_HEADERS,
    )
    after_accept = await snapshot(db)

    row = (await db.exec(select(StudyPlan).where(StudyPlan.id == uuid.UUID(plan["id"])))).one()
    # SQLite does not enforce FKs by default, so the links are removed explicitly
    # here the same way seed.retire_from_file does it. Issued as a bulk DELETE
    # rather than per-object, because on Postgres the plan's own cascade may have
    # already taken them and the ORM would warn about the missing rows.
    await db.exec(
        delete(StudyPlanCardLink).where(col(StudyPlanCardLink.plan_id) == uuid.UUID(plan["id"]))
    )
    await db.delete(row)
    await db.commit()

    assert await snapshot(db) == after_accept
