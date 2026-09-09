"""Owner-scoped discovery for unfinished attempts and archived cards."""

import uuid

import pytest

from app.models import CARD_ARCHIVED, STATUS_AWAITING_FOLLOW_UP, Session, SessionProbe, User
from tests.conftest import API_HEADERS, make_card


async def test_archive_listing_is_explicit_and_owner_scoped(client, db):
    other = User(id=uuid.uuid4())
    db.add(other)
    await db.flush()
    active = make_card()
    archived = make_card(topic="Archived mechanism", lifecycle_status=CARD_ARCHIVED)
    foreign = make_card(user_id=other.id, lifecycle_status=CARD_ARCHIVED)
    db.add_all([active, archived, foreign])
    await db.commit()

    regular = await client.get("/cards", headers=API_HEADERS)
    recovery = await client.get("/cards?lifecycle=archived", headers=API_HEADERS)
    due = await client.get("/cards/due", headers=API_HEADERS)

    assert [row["id"] for row in regular.json()] == [str(active.id)]
    assert [row["id"] for row in recovery.json()] == [str(archived.id)]
    assert recovery.json()[0]["lifecycle_status"] == "archived"
    assert str(archived.id) not in {row["id"] for row in due.json()}
    assert (await client.get("/cards?lifecycle=all", headers=API_HEADERS)).status_code == 422
    assert (await client.get("/cards?lifecycle=archived")).status_code == 401
    assert (await client.get(f"/cards/{foreign.id}", headers=API_HEADERS)).status_code == 404


@pytest.mark.parametrize("practice", [False, True])
async def test_history_identifies_live_turn_and_preserves_abandoned_partial(client, db, practice):
    card = make_card()
    db.add(card)
    await db.flush()
    session = Session(
        card_id=card.id, question_asked="Explain the mechanism.",
        answer_text="My first answer", draft_text="My unfinished spoken probe",
        status=STATUS_AWAITING_FOLLOW_UP, practice=practice, follow_up_used=True,
    )
    db.add(session)
    await db.flush()
    db.add(SessionProbe(session_id=session.id, idx=1, question="What fails?"))
    await db.commit()
    await db.refresh(card)
    before = card.model_dump()

    history = (await client.get(f"/cards/{card.id}", headers=API_HEADERS)).json()
    assert history["active_session"] == {
        "id": str(session.id), "practice": practice, "turn_index": 1,
    }
    assert history["learning_available"] is False
    assert history["sessions"][0]["unscored_draft"] is None

    for _ in range(2):
        response = await client.post(f"/sessions/{session.id}/abandon", headers=API_HEADERS)
        assert response.status_code == 204
    history = (await client.get(f"/cards/{card.id}", headers=API_HEADERS)).json()
    assert history["active_session"] is None
    saved = history["sessions"][0]
    assert saved["status"] == "abandoned"
    assert saved["unscored_draft"] == "My unfinished spoken probe"
    assert saved["score"] is None
    assert not any(turn["role"] == "score" for turn in saved["turns"])
    await db.refresh(card)
    assert card.model_dump() == before


async def test_completed_session_is_never_offered_as_an_unfinished_attempt(client, db):
    card = make_card()
    db.add(card)
    await db.flush()
    db.add(Session(card_id=card.id, question_asked="Question", status="complete", score=3))
    await db.commit()
    history = (await client.get(f"/cards/{card.id}", headers=API_HEADERS)).json()
    assert history["active_session"] is None
    assert history["sessions"][0]["status"] == "complete"
