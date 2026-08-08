import uuid
from datetime import date

from sqlalchemy import text
from sqlmodel import select

from app.config import get_settings
from app.models import (
    AppleIdentity,
    AuthSession,
    Card,
    Settings,
    StudyPlan,
    User,
)
from app.services import authentication


async def _account(db, *, topic: str) -> tuple[User, dict[str, str], Card]:
    user = User()
    db.add(user)
    await db.flush()
    db.add(Settings(user_id=user.id, timezone="UTC"))
    card = Card(
        user_id=user.id,
        topic=topic,
        category="Unsorted",
        canonical_question=f"Explain {topic}.",
        next_review_at=date.today(),
    )
    db.add(card)
    pair = await authentication.issue_session(db, user.id, get_settings())
    await db.commit()
    return user, {"Authorization": f"Bearer {pair.access_token}"}, card


async def test_nonce_is_public_and_single_use(client, db, monkeypatch):
    first = await client.post("/auth/nonce")
    assert first.status_code == 200
    nonce = first.json()["nonce"]

    async def verified(_identity_token, supplied_nonce, _config):
        assert supplied_nonce == nonce
        return authentication.AppleClaims(subject="apple-subject-1", email="relay@example.com")

    async def exchanged(_code, _config):
        return None

    monkeypatch.setattr(authentication, "verify_apple_identity_token", verified)
    monkeypatch.setattr(authentication, "exchange_apple_code", exchanged)

    body = {
        "identity_token": "signed-token",
        "authorization_code": "single-use-code",
        "nonce": nonce,
        "display_name": "Casey",
    }
    signed_in = await client.post("/auth/apple", json=body)
    assert signed_in.status_code == 200
    tokens = signed_in.json()
    assert tokens["token_type"] == "bearer"

    me = await client.get("/auth/me", headers={"Authorization": f"Bearer {tokens['access_token']}"})
    assert me.status_code == 200

    identity = (await db.exec(select(AppleIdentity))).one()
    assert identity.subject == "apple-subject-1"
    assert identity.email == "relay@example.com"
    assert identity.display_name == "Casey"
    assert (await db.exec(select(Settings).where(Settings.user_id == identity.user_id))).one()

    replay = await client.post("/auth/apple", json=body)
    assert replay.status_code == 401


def test_apple_receives_a_sha256_nonce_while_devmax_keeps_the_raw_value():
    raw = "single-use-raw-nonce"
    assert authentication.apple_nonce(raw) == authentication.token_hash(raw)
    assert authentication.apple_nonce(raw) != raw


async def test_refresh_rotates_and_replay_revokes_the_family(client, db):
    user = User()
    db.add(user)
    await db.flush()
    pair = await authentication.issue_session(db, user.id, get_settings())
    await db.commit()

    rotated = await client.post("/auth/refresh", json={"refresh_token": pair.refresh_token})
    assert rotated.status_code == 200
    replacement = rotated.json()
    assert replacement["refresh_token"] != pair.refresh_token

    replay = await client.post("/auth/refresh", json={"refresh_token": pair.refresh_token})
    assert replay.status_code == 401

    # Replaying the predecessor revokes its replacement too, not only the old row.
    me = await client.get(
        "/auth/me",
        headers={"Authorization": f"Bearer {replacement['access_token']}"},
    )
    assert me.status_code == 401
    family = (await db.exec(select(AuthSession).where(AuthSession.user_id == user.id))).all()
    assert len(family) == 2
    assert all(row.revoked_at is not None for row in family)


async def test_cards_sessions_settings_and_plans_are_isolated(client, db):
    first, first_headers, first_card = await _account(db, topic="First user's topic")
    second, second_headers, second_card = await _account(db, topic="Second user's topic")

    first_due = (await client.get("/cards/due", headers=first_headers)).json()
    second_due = (await client.get("/cards/due", headers=second_headers)).json()
    assert [row["topic"] for row in first_due] == ["First user's topic"]
    assert [row["topic"] for row in second_due] == ["Second user's topic"]

    assert (await client.get(f"/cards/{second_card.id}", headers=first_headers)).status_code == 404
    assert (
        await client.post(f"/cards/{second_card.id}/sessions", headers=first_headers)
    ).status_code == 404

    started = await client.post(f"/cards/{first_card.id}/sessions", headers=first_headers)
    assert started.status_code == 200
    session_id = started.json()["session_id"]
    assert (
        await client.patch(
            f"/sessions/{session_id}/draft",
            headers=second_headers,
            json={"draft_text": "not mine"},
        )
    ).status_code == 404

    first_settings = await client.put(
        "/settings",
        headers=first_headers,
        json={
            "reviews_per_day": 5,
            "timezone": "America/New_York",
            "windows": [{"label": "Morning", "from": "07:00", "to": "08:00", "on": True}],
        },
    )
    assert first_settings.status_code == 200
    assert (await client.get("/settings", headers=second_headers)).json()["reviews_per_day"] == 2

    plan = StudyPlan(
        user_id=second.id,
        title="Private plan",
        subject="Law",
        subject_slug="law",
        guide_text="source",
        status="paused",
        mode="flexible",
        start_date=date.today(),
        default_weekly_capacity_minutes=60,
        current_week_index=1,
        forecast_end_plan_week=1,
    )
    db.add(plan)
    await db.commit()
    assert (await client.get(f"/study-plans/{plan.id}", headers=first_headers)).status_code == 404

    # The account objects themselves remain distinct throughout the request walk.
    assert first.id != second.id


async def test_legacy_api_key_can_only_see_the_founder_account(client, db):
    _, _, foreign = await _account(db, topic="Not founder data")
    from tests.conftest import API_HEADERS

    assert (await client.get(f"/cards/{foreign.id}", headers=API_HEADERS)).status_code == 404


async def test_onboarding_completion_and_export_are_account_scoped(client, db):
    user, headers, card = await _account(db, topic="Exported topic")
    profile = await client.post("/auth/onboarding/complete", headers=headers)
    assert profile.status_code == 200
    assert profile.json()["onboarding_completed"] is True

    exported = await client.get("/auth/export", headers=headers)
    assert exported.status_code == 200
    body = exported.json()
    assert body["account"]["id"] == str(user.id)
    assert [row["id"] for row in body["cards"]] == [str(card.id)]
    assert all(row["topic"] != "Consistent hashing" for row in body["cards"])


async def test_account_deletion_revokes_apple_before_cascading(client, db, monkeypatch):
    if db.bind.dialect.name == "sqlite":
        await db.exec(text("PRAGMA foreign_keys=ON"))
    user, headers, _ = await _account(db, topic="Deleted topic")
    db.add(
        AppleIdentity(
            user_id=user.id,
            subject="delete-subject",
            apple_refresh_token="encrypted-refresh",
        )
    )
    await db.commit()
    revoked = []

    async def revoke(token, _config):
        revoked.append(token)

    monkeypatch.setattr(
        authentication, "decrypt_apple_token", lambda _token, _config: "apple-refresh"
    )
    monkeypatch.setattr(authentication, "revoke_apple_authorization", revoke)
    response = await client.delete("/auth/account", headers=headers)
    assert response.status_code == 204
    assert revoked == ["apple-refresh"]
    assert await db.get(User, user.id) is None
    assert not (await db.exec(select(Card).where(Card.user_id == user.id))).all()


async def test_invalid_bearer_token_is_rejected(client):
    response = await client.get("/cards/due", headers={"Authorization": f"Bearer {uuid.uuid4()}"})
    assert response.status_code == 401
