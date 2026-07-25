from datetime import date, datetime
from zoneinfo import ZoneInfo

from fastapi import Depends
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.db import get_session
from app.models import Settings

SessionDep = Depends(get_session)


async def get_settings_row(db: AsyncSession) -> Settings:
    row = (await db.exec(select(Settings).where(Settings.id == 1))).first()
    if row is None:  # pragma: no cover — seeded by migration 0001
        row = Settings(id=1)
        db.add(row)
        await db.commit()
    return row


def now_in(tz: str) -> datetime:
    return datetime.now(ZoneInfo(tz))


async def local_today(db: AsyncSession) -> date:
    """Every due comparison is against the user's local calendar day, not UTC."""
    return now_in((await get_settings_row(db)).timezone).date()
