"""Explicit, versioned permission for third-party AI processing."""

import hashlib
import uuid
from datetime import UTC, datetime

from fastapi import HTTPException
from sqlalchemy import text
from sqlmodel.ext.asyncio.session import AsyncSession

from app.config import Settings
from app.consent_policy import (
    LATEST_POLICY_VERSION,
    LEGACY_POLICY_VERSION,
    POLICIES_BY_VERSION,
    policy_for,
    satisfies,
)
from app.models import (
    AI_CONSENT_DECLINED,
    AI_CONSENT_GRANTED,
    AI_CONSENT_WITHDRAWN,
    AIConsentEvent,
    User,
)

# Backward-compatible aliases for callers that mean "latest understood by this
# source tree." Activation is separately owned by Settings so merging code cannot
# silently make an already-installed client stale.
POLICY_VERSION = LATEST_POLICY_VERSION
PROVIDER = policy_for(POLICY_VERSION).provider
RECORDED_STATUSES = frozenset(
    {AI_CONSENT_GRANTED, AI_CONSENT_DECLINED, AI_CONSENT_WITHDRAWN}
)


def _now() -> datetime:
    return datetime.now(UTC)


def processing_allowed(user: User, required_policy_version: str = POLICY_VERSION) -> bool:
    return (
        user.ai_consent_status == AI_CONSENT_GRANTED
        and satisfies(user.ai_consent_version, required_policy_version)
        and user.ai_consent_granted_at is not None
    )


def prompt_required(user: User, required_policy_version: str = POLICY_VERSION) -> bool:
    # A refusal never authorizes provider work, so it remains safe across a
    # disclosure expansion. Do not trap an older client on the consent screen;
    # the next explicit attempt to use AI can present the newer disclosure.
    if user.ai_consent_status in {AI_CONSENT_DECLINED, AI_CONSENT_WITHDRAWN}:
        return False
    return (
        not satisfies(user.ai_consent_version, required_policy_version)
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
    required_policy_version: str = POLICY_VERSION,
) -> tuple[User, datetime]:
    user = await lock_user_boundary(db, user_id)
    if user is None:
        raise HTTPException(status_code=401, detail="unauthorized")

    required_policy = policy_for(required_policy_version)
    if action == "withdraw":
        # An old or partially upgraded client must always be able to revoke.
        # Preserve the disclosure it had recorded when possible; otherwise use
        # the deployment's required policy solely as event metadata.
        recorded_policy_version = (
            user.ai_consent_version
            if user.ai_consent_version in POLICIES_BY_VERSION
            else required_policy_version
        )
    else:
        # The pre-versioned v1 client rendered the Anthropic-only disclosure and
        # omitted policy_version. Treat that exact legacy shape as v1. Any named
        # unknown version still fails closed.
        recorded_policy_version = policy_version or LEGACY_POLICY_VERSION
        known_policy = recorded_policy_version in POLICIES_BY_VERSION
        # A legacy decline grants nothing and remains a valid way to continue
        # without AI. A grant must cover every provider in the required policy.
        if not known_policy or (
            action == "grant"
            and not satisfies(recorded_policy_version, required_policy_version)
        ):
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "ai_consent_policy_version_mismatch",
                    "provider": required_policy.provider,
                    "policy_version": required_policy.version,
                },
            )

    recorded_policy = policy_for(recorded_policy_version)

    now = _now()
    user.ai_consent_status = {
        "grant": AI_CONSENT_GRANTED,
        "decline": AI_CONSENT_DECLINED,
        "withdraw": AI_CONSENT_WITHDRAWN,
    }[action]
    user.ai_consent_version = recorded_policy.version
    user.ai_consent_updated_at = now
    user.ai_consent_granted_at = now if action == "grant" else None
    user.updated_at = now
    db.add(user)
    db.add(
        AIConsentEvent(
            user_id=user_id,
            provider=recorded_policy.provider,
            policy_version=recorded_policy.version,
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
    if config.ai_consent_enforcement_enabled and not processing_allowed(
        user, config.ai_consent_required_policy_version
    ):
        required_policy = policy_for(config.ai_consent_required_policy_version)
        raise HTTPException(
            status_code=403,
            detail={
                "code": "ai_consent_required",
                "provider": required_policy.provider,
                "policy_version": required_policy.version,
            },
        )
