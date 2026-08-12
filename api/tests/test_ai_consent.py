from datetime import UTC, datetime, timedelta

import pytest
from fastapi import HTTPException
from sqlmodel import select

from app.config import get_settings
from app.models import FOUNDER_USER_ID, AIConsentEvent, AppleIdentity, AuthSession, User
from app.services import ai_consent, authentication, usage
from tests.conftest import API_HEADERS


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


async def test_profile_prompts_until_the_current_anthropic_disclosure_is_recorded(
    client,
):
    pending = await client.get("/auth/me", headers=API_HEADERS)
    assert pending.status_code == 200
    assert pending.json()["ai_consent_status"] == "pending"
    assert pending.json()["ai_consent_version"] == ""
    assert pending.json()["ai_processing_allowed"] is False
    assert pending.json()["ai_consent_prompt_required"] is True

    granted = await client.put(
        "/auth/ai-consent", headers=API_HEADERS, json={"action": "grant"}
    )
    assert granted.status_code == 200
    assert granted.json()["provider"] == "Anthropic"
    assert granted.json()["policy_version"] == ai_consent.POLICY_VERSION
    assert granted.json()["status"] == "granted"
    assert granted.json()["processing_allowed"] is True
    assert granted.json()["prompt_required"] is False

    profile = await client.get("/auth/me", headers=API_HEADERS)
    assert profile.json()["ai_processing_allowed"] is True
    assert profile.json()["ai_consent_prompt_required"] is False

    exported = await client.get("/auth/export", headers=API_HEADERS)
    assert exported.status_code == 200
    assert exported.json()["ai_consent_events"][-1]["action"] == "grant"
    assert exported.json()["ai_consent_events"][-1]["policy_version"] == (
        ai_consent.POLICY_VERSION
    )


@pytest.mark.parametrize("action", ["decline", "withdraw"])
async def test_decline_and_withdraw_are_recorded_and_block_processing(client, db, action):
    if action == "withdraw":
        await client.put(
            "/auth/ai-consent", headers=API_HEADERS, json={"action": "grant"}
        )
    response = await client.put(
        "/auth/ai-consent", headers=API_HEADERS, json={"action": action}
    )
    assert response.status_code == 200
    assert response.json()["status"] == (
        "declined" if action == "decline" else "withdrawn"
    )
    assert response.json()["processing_allowed"] is False
    assert response.json()["prompt_required"] is False

    events = (
        await db.exec(
            select(AIConsentEvent)
            .where(AIConsentEvent.user_id == FOUNDER_USER_ID)
            .order_by(AIConsentEvent.created_at)
        )
    ).all()
    assert events[-1].provider == "Anthropic"
    assert events[-1].policy_version == ai_consent.POLICY_VERSION
    assert events[-1].action == action


async def test_model_budget_gate_refuses_without_current_permission(db):
    enforced = get_settings().model_copy(update={"ai_consent_enforcement_enabled": True})
    with pytest.raises(HTTPException) as exc:
        await usage.ensure_available(db, FOUNDER_USER_ID, "score_v2", enforced)
    assert exc.value.status_code == 403
    assert exc.value.detail["code"] == "ai_consent_required"

    await ai_consent.record(db, FOUNDER_USER_ID, "grant")
    await usage.ensure_available(db, FOUNDER_USER_ID, "score_v2", enforced)


async def test_apple_revocation_notification_invalidates_sessions_but_preserves_data(
    client, db, monkeypatch
):
    identity = AppleIdentity(
        user_id=FOUNDER_USER_ID,
        subject="apple-revoked-subject",
        apple_refresh_token="encrypted-refresh",
        last_apple_event_at=datetime.now(UTC) - timedelta(minutes=10),
    )
    now = datetime.now(UTC)
    session = AuthSession(
        user_id=FOUNDER_USER_ID,
        access_token_hash=authentication.token_hash("access"),
        refresh_token_hash=authentication.token_hash("refresh"),
        access_expires_at=now + timedelta(minutes=15),
        refresh_expires_at=now + timedelta(days=30),
    )
    db.add(identity)
    db.add(session)
    await db.commit()

    occurred_at = datetime.now(UTC) - timedelta(minutes=5)

    async def verified(_payload, _config):
        return authentication.AppleAccountEvent(
            event_type="consent-revoked",
            subject=identity.subject,
            occurred_at=occurred_at,
        )

    monkeypatch.setattr(authentication, "verify_apple_server_notification", verified)
    response = await client.post(
        "/auth/apple/notifications", json={"payload": "signed-by-apple"}
    )
    assert response.status_code == 204

    await db.refresh(identity)
    await db.refresh(session)
    assert identity.apple_refresh_token is None
    assert _as_utc(identity.authorization_revoked_at) == occurred_at
    assert _as_utc(identity.last_apple_event_at) == occurred_at
    assert session.revoked_at is not None
    assert await db.get(User, FOUNDER_USER_ID) is not None


async def test_an_old_apple_notification_cannot_revoke_a_new_authorization(
    client, db, monkeypatch
):
    now = datetime.now(UTC)
    identity = AppleIdentity(
        user_id=FOUNDER_USER_ID,
        subject="apple-current-subject",
        apple_refresh_token="fresh-encrypted-refresh",
        last_apple_event_at=now,
    )
    session = AuthSession(
        user_id=FOUNDER_USER_ID,
        access_token_hash=authentication.token_hash("current-access"),
        refresh_token_hash=authentication.token_hash("current-refresh"),
        access_expires_at=now + timedelta(minutes=15),
        refresh_expires_at=now + timedelta(days=30),
    )
    db.add(identity)
    db.add(session)
    await db.commit()

    async def verified(_payload, _config):
        return authentication.AppleAccountEvent(
            event_type="consent-revoked",
            subject=identity.subject,
            occurred_at=now - timedelta(minutes=1),
        )

    monkeypatch.setattr(authentication, "verify_apple_server_notification", verified)
    response = await client.post(
        "/auth/apple/notifications", json={"payload": "delayed-apple-retry"}
    )
    assert response.status_code == 204

    await db.refresh(identity)
    await db.refresh(session)
    assert identity.apple_refresh_token == "fresh-encrypted-refresh"
    assert identity.authorization_revoked_at is None
    assert session.revoked_at is None


async def test_apple_notifications_apply_only_the_newest_signed_event(
    client, db, monkeypatch
):
    now = datetime.now(UTC)
    identity = AppleIdentity(
        user_id=FOUNDER_USER_ID,
        subject="apple-out-of-order-subject",
        email="private-relay@example.com",
        apple_refresh_token="fresh-encrypted-refresh",
        last_apple_event_at=now - timedelta(minutes=20),
    )
    session = AuthSession(
        user_id=FOUNDER_USER_ID,
        access_token_hash=authentication.token_hash("out-of-order-access"),
        refresh_token_hash=authentication.token_hash("out-of-order-refresh"),
        access_expires_at=now + timedelta(minutes=15),
        refresh_expires_at=now + timedelta(days=30),
    )
    db.add(identity)
    db.add(session)
    await db.commit()

    events = iter(
        [
            authentication.AppleAccountEvent(
                event_type="email-disabled",
                subject=identity.subject,
                occurred_at=now - timedelta(minutes=5),
            ),
            authentication.AppleAccountEvent(
                event_type="consent-revoked",
                subject=identity.subject,
                occurred_at=now - timedelta(minutes=10),
            ),
        ]
    )

    async def verified(_payload, _config):
        return next(events)

    monkeypatch.setattr(authentication, "verify_apple_server_notification", verified)
    first = await client.post(
        "/auth/apple/notifications", json={"payload": "newer-email-event"}
    )
    second = await client.post(
        "/auth/apple/notifications", json={"payload": "older-revocation-event"}
    )
    assert first.status_code == 204
    assert second.status_code == 204

    await db.refresh(identity)
    await db.refresh(session)
    assert identity.email is None
    assert identity.apple_refresh_token == "fresh-encrypted-refresh"
    assert identity.authorization_revoked_at is None
    assert _as_utc(identity.last_apple_event_at) == now - timedelta(minutes=5)
    assert session.revoked_at is None


async def test_apple_notification_requires_a_valid_apple_signature(client, monkeypatch):
    async def rejected(_payload, _config):
        raise authentication.AuthenticationError

    monkeypatch.setattr(authentication, "verify_apple_server_notification", rejected)
    response = await client.post(
        "/auth/apple/notifications", json={"payload": "not-apple"}
    )
    assert response.status_code == 401


async def test_apple_notification_verifier_requires_the_signed_event_timestamp(
    monkeypatch,
):
    config = get_settings().model_copy(
        update={
            "apple_client_id": "com.christrinh.devmax",
            "apple_team_id": "team",
            "apple_key_id": "key",
            "apple_private_key": "configured",
            "auth_encryption_key": "configured",
        }
    )

    class SigningKey:
        key = "apple-public-key"

    monkeypatch.setattr(
        authentication.APPLE_JWK_CLIENT,
        "get_signing_key_from_jwt",
        lambda _payload: SigningKey(),
    )
    captured = {}

    def decoded(_payload, key, **kwargs):
        captured.update(key=key, **kwargs)
        return {
            "events": {
                "type": "consent-revoked",
                "sub": "apple-subject",
                "event_time": 1_786_553_600,
            }
        }

    monkeypatch.setattr(authentication.jwt, "decode", decoded)
    event = await authentication.verify_apple_server_notification("signed", config)
    assert event.event_type == "consent-revoked"
    assert event.subject == "apple-subject"
    assert event.occurred_at == datetime.fromtimestamp(1_786_553_600, UTC)
    assert captured["key"] == "apple-public-key"
    assert captured["algorithms"] == ["RS256"]
    assert captured["audience"] == "com.christrinh.devmax"
    assert captured["issuer"] == authentication.APPLE_ISSUER

    def missing_time(*_args, **_kwargs):
        return {"events": {"type": "consent-revoked", "sub": "apple-subject"}}

    monkeypatch.setattr(authentication.jwt, "decode", missing_time)
    with pytest.raises(authentication.AuthenticationError):
        await authentication.verify_apple_server_notification("signed", config)
