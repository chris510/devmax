import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.config import get_settings
from app.models import (
    AI_CONSENT_GRANTED,
    FOUNDER_USER_ID,
    AIConsentEvent,
    AppleIdentity,
    AuthSession,
    LLMUsage,
    User,
)
from app.services import ai_consent, authentication, llm, usage
from tests.conftest import API_HEADERS, TEST_DATABASE_URL


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


async def test_profile_prompts_until_the_current_provider_disclosure_is_recorded(
    client,
):
    pending = await client.get("/auth/me", headers=API_HEADERS)
    assert pending.status_code == 200
    assert pending.json()["ai_consent_status"] == "pending"
    assert pending.json()["ai_consent_version"] == ""
    assert pending.json()["ai_processing_allowed"] is False
    assert pending.json()["ai_consent_prompt_required"] is True

    granted = await client.put(
        "/auth/ai-consent",
        headers=API_HEADERS,
        json={"action": "grant", "policy_version": ai_consent.POLICY_VERSION},
    )
    assert granted.status_code == 200
    assert granted.json()["provider"] == ai_consent.PROVIDER
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


async def test_an_old_anthropic_grant_requires_the_combined_disclosure(client, db):
    user = await db.get(User, FOUNDER_USER_ID)
    assert user is not None
    user.ai_consent_status = AI_CONSENT_GRANTED
    user.ai_consent_version = "anthropic-2026-08-12-v1"
    user.ai_consent_granted_at = datetime.now(UTC)
    db.add(user)
    await db.commit()

    profile = await client.get("/auth/me", headers=API_HEADERS)

    assert profile.status_code == 200
    assert profile.json()["ai_processing_allowed"] is False
    assert profile.json()["ai_consent_prompt_required"] is True


async def test_decline_is_recorded_and_blocks_processing(client, db):
    response = await client.put(
        "/auth/ai-consent",
        headers=API_HEADERS,
        json={"action": "decline", "policy_version": ai_consent.POLICY_VERSION},
    )
    assert response.status_code == 200
    assert response.json()["status"] == "declined"
    assert response.json()["processing_allowed"] is False
    assert response.json()["prompt_required"] is False

    events = (
        await db.exec(
            select(AIConsentEvent)
            .where(AIConsentEvent.user_id == FOUNDER_USER_ID)
            .order_by(AIConsentEvent.created_at)
        )
    ).all()
    assert events[-1].provider == ai_consent.PROVIDER
    assert events[-1].policy_version == ai_consent.POLICY_VERSION
    assert events[-1].action == "decline"


@pytest.mark.parametrize("policy_version", [None, "anthropic-2026-08-12-v1"])
async def test_withdraw_remains_available_without_the_current_disclosure(
    client, db, policy_version
):
    await client.put(
        "/auth/ai-consent",
        headers=API_HEADERS,
        json={"action": "grant", "policy_version": ai_consent.POLICY_VERSION},
    )
    body = {"action": "withdraw"}
    if policy_version is not None:
        body["policy_version"] = policy_version
    response = await client.put("/auth/ai-consent", headers=API_HEADERS, json=body)

    assert response.status_code == 200
    assert response.json()["status"] == "withdrawn"
    assert response.json()["processing_allowed"] is False

    events = (
        await db.exec(
            select(AIConsentEvent)
            .where(AIConsentEvent.user_id == FOUNDER_USER_ID)
            .order_by(AIConsentEvent.created_at)
        )
    ).all()
    assert events[-1].provider == ai_consent.PROVIDER
    assert events[-1].policy_version == ai_consent.POLICY_VERSION
    assert events[-1].action == "withdraw"


@pytest.mark.parametrize("action", ["grant", "decline"])
@pytest.mark.parametrize("policy_version", [None, "anthropic-2026-08-12-v1"])
async def test_grant_and_decline_reject_missing_or_stale_disclosures_before_recording(
    client, db, action, policy_version
):
    body = {"action": action}
    if policy_version is not None:
        body["policy_version"] = policy_version
    response = await client.put("/auth/ai-consent", headers=API_HEADERS, json=body)

    assert response.status_code == 409
    assert response.json()["detail"] == {
        "code": "ai_consent_policy_version_mismatch",
        "provider": ai_consent.PROVIDER,
        "policy_version": ai_consent.POLICY_VERSION,
    }
    user = await db.get(User, FOUNDER_USER_ID)
    assert user is not None
    assert user.ai_consent_status == "pending"
    assert (await db.exec(select(AIConsentEvent))).all() == []


async def test_model_budget_gate_refuses_without_current_permission(db):
    enforced = get_settings().model_copy(update={"ai_consent_enforcement_enabled": True})
    with pytest.raises(HTTPException) as exc:
        await usage.ensure_available(db, FOUNDER_USER_ID, "score_v2", enforced)
    assert exc.value.status_code == 403
    assert exc.value.detail["code"] == "ai_consent_required"

    await ai_consent.record(
        db, FOUNDER_USER_ID, "grant", ai_consent.POLICY_VERSION
    )
    await usage.ensure_available(db, FOUNDER_USER_ID, "score_v2", enforced)


async def test_provider_boundary_rechecks_consent_under_the_database_lock(
    db, monkeypatch
):
    await ai_consent.record(
        db, FOUNDER_USER_ID, "grant", ai_consent.POLICY_VERSION
    )
    original_get = db.get
    calls: list[dict] = []

    async def locked_get(*args, **kwargs):
        calls.append(kwargs)
        return await original_get(*args, **kwargs)

    monkeypatch.setattr(db, "get", locked_get)
    enforced = get_settings().model_copy(update={"ai_consent_enforcement_enabled": True})

    await ai_consent.require_ai_processing(db, FOUNDER_USER_ID, enforced)

    if db.bind is not None and db.bind.dialect.name == "postgresql":
        assert "with_for_update" not in calls[-1]
    else:
        assert calls[-1]["with_for_update"] == {"key_share": True}
    assert calls[-1]["populate_existing"] is True


async def test_long_guide_call_is_authorized_at_the_physical_provider_boundary(db):
    await ai_consent.record(
        db, FOUNDER_USER_ID, "grant", ai_consent.POLICY_VERSION
    )
    enforced = get_settings().model_copy(update={"ai_consent_enforcement_enabled": True})
    authorize = usage.provider_call_authorizer(
        db,
        FOUNDER_USER_ID,
        "guide_import",
        config=enforced,
        provider="anthropic",
        model="claude-test",
    )
    await authorize(1)

    row = (await db.exec(select(LLMUsage))).one()
    assert row.operation == "guide_import"
    assert row.details["audit_type"] == "provider_call_authorization"
    assert row.details["outcome"] == "authorized"
    assert row.details["provider"] == "anthropic"
    assert row.details["model"] == "claude-test"
    assert row.details["provider_attempt"] == 1
    assert row.details["ai_consent_policy_version"] == ai_consent.POLICY_VERSION
    assert row.details["ai_consent_verified"] is True
    assert row.details["authorized_at"].endswith("+00:00")


async def test_withdrawal_between_import_attempts_blocks_the_next_transmission(db):
    await ai_consent.record(
        db, FOUNDER_USER_ID, "grant", ai_consent.POLICY_VERSION
    )
    enforced = get_settings().model_copy(update={"ai_consent_enforcement_enabled": True})
    authorize = usage.provider_call_authorizer(
        db,
        FOUNDER_USER_ID,
        "guide_import",
        config=enforced,
        provider="anthropic",
        model="claude-test",
    )
    await authorize(1)
    await ai_consent.record(db, FOUNDER_USER_ID, "withdraw", None)

    with pytest.raises(HTTPException) as exc:
        await authorize(2)

    assert exc.value.status_code == 403
    rows = (await db.exec(select(LLMUsage))).all()
    assert len(rows) == 1
    assert rows[0].details["provider_attempt"] == 1


@pytest.mark.parametrize("concurrent_action", ["withdraw", "delete"])
async def test_postgres_long_call_releases_the_account_lock_while_provider_runs(
    db, concurrent_action
):
    """The boundary is serialized, but the multi-minute await pins no DB lock."""
    if not TEST_DATABASE_URL.startswith("postgresql"):
        pytest.skip("row-lock concurrency requires Postgres")

    await ai_consent.record(
        db, FOUNDER_USER_ID, "grant", ai_consent.POLICY_VERSION
    )
    engine = create_async_engine(TEST_DATABASE_URL)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    provider_entered = asyncio.Event()
    release_provider = asyncio.Event()

    async def create(**_kwargs):
        provider_entered.set()
        await release_provider.wait()
        return SimpleNamespace(
            content=[SimpleNamespace(type="text", text='{"result":"ok"}')],
            usage=SimpleNamespace(
                input_tokens=1,
                output_tokens=1,
                cache_read_input_tokens=0,
                cache_creation_input_tokens=0,
            ),
            stop_reason="end_turn",
            _request_id="msg_consent_concurrency",
        )

    fake_client = SimpleNamespace(messages=SimpleNamespace(create=create))
    enforced = get_settings().model_copy(update={"ai_consent_enforcement_enabled": True})

    try:
        async with factory() as call_db, factory() as control_db:
            authorize = usage.provider_call_authorizer(
                call_db,
                FOUNDER_USER_ID,
                "guide_import",
                config=enforced,
                provider="anthropic",
                model="claude-test",
            )
            provider_task = asyncio.create_task(
                llm._complete(
                    model="claude-test",
                    effort=None,
                    rubric="test",
                    user_content="test",
                    schema={"type": "object"},
                    max_tokens=16,
                    retry=False,
                    before_provider_call=authorize,
                    client_override=fake_client,
                )
            )
            await asyncio.wait_for(provider_entered.wait(), timeout=2)

            if concurrent_action == "withdraw":
                await asyncio.wait_for(
                    ai_consent.record(control_db, FOUNDER_USER_ID, "withdraw", None),
                    timeout=2,
                )
            else:
                user = await ai_consent.lock_user_boundary(
                    control_db, FOUNDER_USER_ID
                )
                assert user is not None
                await control_db.delete(user)
                await asyncio.wait_for(control_db.commit(), timeout=2)

            # Withdrawal/deletion completed without waiting for the simulated
            # 11-minute provider response.
            assert not provider_task.done()
            release_provider.set()
            assert await asyncio.wait_for(provider_task, timeout=2) == {"result": "ok"}

            account_exists = await usage.lock_account_for_provider_result(
                call_db, FOUNDER_USER_ID
            )
            await call_db.rollback()
            assert account_exists is (concurrent_action == "withdraw")

        async with factory() as verify_db:
            user = await verify_db.get(User, FOUNDER_USER_ID)
            rows = (await verify_db.exec(select(LLMUsage))).all()
            if concurrent_action == "withdraw":
                assert user is not None
                assert user.ai_consent_status == "withdrawn"
                assert len(rows) == 1
            else:
                assert user is None
                assert rows == []
    finally:
        release_provider.set()
        await engine.dispose()


async def test_postgres_queued_delete_cannot_block_independent_provider_audit(db):
    """Regression: User row-lock queueing made this a three-way deadlock."""
    if not TEST_DATABASE_URL.startswith("postgresql"):
        pytest.skip("advisory-lock concurrency requires Postgres")

    await ai_consent.record(
        db, FOUNDER_USER_ID, "grant", ai_consent.POLICY_VERSION
    )
    engine = create_async_engine(TEST_DATABASE_URL)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    enforced = get_settings().model_copy(update={"ai_consent_enforcement_enabled": True})

    try:
        async with factory() as provider_db, factory() as delete_db:
            await ai_consent.require_ai_processing(
                provider_db, FOUNDER_USER_ID, enforced
            )
            scoring_event_id = str(uuid.uuid4())
            intent_id = await usage.record_scoring_intent(
                provider_db,
                FOUNDER_USER_ID,
                details={
                    "status": "pending",
                    "reserved_calls": 1,
                    "scoring_event_id": scoring_event_id,
                    "expected_calls": [
                        {
                            "provider": "anthropic",
                            "model": "claude-test",
                            "requirement": "required",
                        }
                    ],
                },
            )

            async def delete_after_boundary() -> None:
                user = await ai_consent.lock_user_boundary(delete_db, FOUNDER_USER_ID)
                assert user is not None
                await delete_db.delete(user)
                await delete_db.commit()

            deletion = asyncio.create_task(delete_after_boundary())
            async with factory() as inspect_db:
                for _ in range(200):
                    waiting = (
                        await inspect_db.exec(
                            text(
                                "SELECT count(*) FROM pg_locks "
                                "WHERE locktype = 'advisory' AND NOT granted"
                            )
                        )
                    ).scalar_one()
                    await inspect_db.rollback()
                    if waiting:
                        break
                    await asyncio.sleep(0.01)
                else:
                    raise AssertionError("delete never queued on the advisory boundary")

            # Deletion is queued, but has not touched the User row.  The
            # independent paid-call audit can therefore take FK KEY SHARE and
            # commit while the provider transaction still owns the advisory.
            await asyncio.wait_for(
                usage.record_physical_calls(
                    provider_db,
                    FOUNDER_USER_ID,
                    "score_v2",
                    intent_id=intent_id,
                    call_details=[
                        {
                            "scoring_event_id": scoring_event_id,
                            "call": {"provider": "anthropic"},
                        }
                    ],
                ),
                timeout=2,
            )
            assert not deletion.done()

            await provider_db.commit()
            await asyncio.wait_for(deletion, timeout=2)

        async with factory() as verify_db:
            assert await verify_db.get(User, FOUNDER_USER_ID) is None
            assert (await verify_db.exec(select(LLMUsage))).all() == []
    finally:
        await engine.dispose()


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
