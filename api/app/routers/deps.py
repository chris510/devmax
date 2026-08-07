from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

from fastapi import Depends
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.auth import current_user_id
from app.db import get_session
from app.models import Settings

SessionDep = Depends(get_session)


async def get_settings_row(db: AsyncSession, user_id=None) -> Settings:
    owner = user_id or current_user_id()
    row = (await db.exec(select(Settings).where(Settings.user_id == owner))).first()
    if row is None:
        row = Settings(user_id=owner)
        db.add(row)
        await db.commit()
    return row


def now_in(tz: str) -> datetime:
    return datetime.now(ZoneInfo(tz))


def as_utc(value: datetime) -> datetime:
    """Normalize a timestamp read back from the database before comparing it.

    Every timestamp is written tz-aware, but SQLite silently drops `tzinfo` on read
    while Postgres keeps it — the same divergence `models.TZ_DATETIME` documents. A
    bare `<` between the two raises TypeError, so comparisons normalize first.
    """
    return value if value.tzinfo else value.replace(tzinfo=UTC)


async def local_today(db: AsyncSession) -> date:
    """Every due comparison is against the user's local calendar day, not UTC."""
    return now_in((await get_settings_row(db)).timezone).date()


async def local_calendar(db: AsyncSession) -> tuple[date, ZoneInfo]:
    """Today *and* the zone it was computed in, for callers that also compare
    stored timestamps against it.

    Every timestamp in the database is UTC, so taking `.date()` off one and
    comparing it to `local_today()` is wrong for the hours where the two
    calendars disagree — west of UTC that's most of the evening, and it reports
    a card reviewed minutes ago as `-1` days since review.
    """
    tz = ZoneInfo((await get_settings_row(db)).timezone)
    return datetime.now(tz).date(), tz
