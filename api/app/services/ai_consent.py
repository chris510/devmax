"""Explicit, versioned permission for third-party AI processing."""

import uuid
from datetime import UTC, datetime

from fastapi import HTTPException
from sqlmodel.ext.asyncio.session import AsyncSession

from app.config import Settings
from app.models import (
    AI_CONSENT_DECLINED,
    AI_CONSENT_GRANTED,
    AI_CONSENT_WITHDRAWN,
    AIConsentEvent,
    User,
)

PROVIDER = "Anthropic"
POLICY_VERSION = "anthropic-2026-08-12-v1"
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


async def record(
    db: AsyncSession, user_id: uuid.UUID, action: str
) -> tuple[User, datetime]:
    user = await db.get(User, user_id, with_for_update=True)
    if user is None:
        raise HTTPException(status_code=401, detail="unauthorized")
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
    if not config.ai_consent_enforcement_enabled:
        return
    user = await db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=401, detail="unauthorized")
    if not processing_allowed(user):
        raise HTTPException(
            status_code=403,
            detail={
                "code": "ai_consent_required",
                "provider": PROVIDER,
                "policy_version": POLICY_VERSION,
            },
        )
