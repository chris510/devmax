"""Explicit, versioned permission for third-party AI processing."""

import hashlib
import uuid
from datetime import UTC, datetime

from fastapi import HTTPException
from sqlalchemy import text
from sqlmodel.ext.asyncio.session import AsyncSession

from app.config import Settings
from app.models import (
    AI_CONSENT_DECLINED,
    AI_CONSENT_GRANTED,
    AI_CONSENT_WITHDRAWN,
    AIConsentEvent,
    User,
)

PROVIDER = "Anthropic and OpenAI"
POLICY_VERSION = "anthropic-openai-2026-08-13-v2"
RECORDED_STATUSES = frozenset(
    {AI_CONSENT_GRANTED, AI_CONSENT_DECLINED, AI_CONSENT_WITHDRAWN}
)


def _now() -> datetime:
    return datetime.now(UTC)


def processing_allowed(user: User) -> bool:
    return (
        user.ai_consent_status == AI_CONSENT_GRANTED
        and user.ai_consent_version == POLICY_VERSION
        and user.ai_consent_granted_at is not None
    )


def prompt_required(user: User) -> bool:
    return (
        user.ai_consent_version != POLICY_VERSION
        or user.ai_consent_status not in RECORDED_STATUSES
    )


def _advisory_key(user_id: uuid.UUID) -> int:
    """A stable signed bigint in a namespace reserved for AI boundaries."""
    digest = hashlib.blake2b(
        user_id.bytes,
        digest_size=8,
        person=b"devmax-ai",
    ).digest()
    return int.from_bytes(digest, byteorder="big", signed=True)


async def lock_user_boundary(db: AsyncSession, user_id: uuid.UUID) -> User | None:
    """Serialize provider authorization, consent mutation, and deletion.

    PostgreSQL uses a transaction-scoped advisory lock instead of a User row
    lock.  That distinction is load-bearing: while deletion waits on the
    advisory lock, an independent provider-audit transaction can still take the
    User foreign key's KEY SHARE lock and commit.  A queued DELETE therefore
    cannot deadlock a provider transaction that is waiting for its audit write.

    SQLite has no advisory locks and is used only as the local/test fallback;
    retain the prior row-lock-shaped read there so the boundary remains explicit.
    """
    bind = db.bind
    if bind is not None and bind.dialect.name == "postgresql":
        await db.exec(
            text("SELECT pg_advisory_xact_lock(:key)"),
            params={"key": _advisory_key(user_id)},
        )
        return await db.get(User, user_id, populate_existing=True)
    return await db.get(
        User,
        user_id,
        with_for_update={"key_share": True},
        populate_existing=True,
    )


async def record(
    db: AsyncSession,
    user_id: uuid.UUID,
    action: str,
    policy_version: str | None,
) -> tuple[User, datetime]:
    user = await lock_user_boundary(db, user_id)
    if user is None:
        raise HTTPException(status_code=401, detail="unauthorized")

    # A grant or decline is meaningful only for the disclosure the person saw.
    # Withdrawal is deliberately exempt: an old or partially upgraded client
    # must always be able to revoke permission for future provider calls.
    if action != "withdraw" and policy_version != POLICY_VERSION:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "ai_consent_policy_version_mismatch",
                "provider": PROVIDER,
                "policy_version": POLICY_VERSION,
            },
        )

    now = _now()
    user.ai_consent_status = {
        "grant": AI_CONSENT_GRANTED,
        "decline": AI_CONSENT_DECLINED,
        "withdraw": AI_CONSENT_WITHDRAWN,
    }[action]
    user.ai_consent_version = POLICY_VERSION
    user.ai_consent_updated_at = now
    user.ai_consent_granted_at = now if action == "grant" else None
    user.updated_at = now
    db.add(user)
    db.add(
        AIConsentEvent(
            user_id=user_id,
            provider=PROVIDER,
            policy_version=POLICY_VERSION,
            action=action,
            created_at=now,
        )
    )
    await db.commit()
    return user, now


async def require_ai_processing(
    db: AsyncSession, user_id: uuid.UUID, config: Settings
) -> None:
    # Keep the current grant stable from this boundary until the caller commits
    # after its provider work. Withdrawal and deletion take the same per-user
    # boundary lock even during the staged compatibility period, so neither can
    # report success in the gap between this check and transmission.
    user = await lock_user_boundary(db, user_id)
    if user is None:
        raise HTTPException(status_code=401, detail="unauthorized")
    if config.ai_consent_enforcement_enabled and not processing_allowed(user):
        raise HTTPException(
            status_code=403,
            detail={
                "code": "ai_consent_required",
                "provider": PROVIDER,
                "policy_version": POLICY_VERSION,
            },
        )
