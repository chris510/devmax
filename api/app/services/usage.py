"""Small account-level budgets around paid model calls."""

import uuid
from datetime import UTC, datetime, timedelta

from fastapi import HTTPException
from sqlalchemy import func
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.config import Settings
from app.models import LLMUsage


async def ensure_available(
    db: AsyncSession, user_id: uuid.UUID, operation: str, config: Settings
) -> None:
    since = datetime.now(UTC) - timedelta(days=1)
    total = (
        await db.exec(
            select(func.count())
            .select_from(LLMUsage)
            .where(LLMUsage.user_id == user_id, LLMUsage.created_at >= since)
        )
    ).one()
    if total >= config.llm_calls_per_day:
        raise HTTPException(status_code=429, detail="daily_model_limit")
    if operation == "guide_import":
        imports = (
            await db.exec(
                select(func.count())
                .select_from(LLMUsage)
                .where(
                    LLMUsage.user_id == user_id,
                    LLMUsage.operation == operation,
                    LLMUsage.created_at >= since,
                )
            )
        ).one()
        if imports >= config.guide_imports_per_day:
            raise HTTPException(status_code=429, detail="daily_import_limit")


def record(db: AsyncSession, user_id: uuid.UUID, operation: str) -> None:
    db.add(LLMUsage(user_id=user_id, operation=operation))
