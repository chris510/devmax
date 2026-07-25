from fastapi import APIRouter, Depends
from sqlmodel.ext.asyncio.session import AsyncSession

from app.db import get_session
from app.routers.deps import get_settings_row
from app.schemas import NotificationWindow, SettingsIn, SettingsOut, window_to_dict

router = APIRouter(tags=["settings"])


def _to_out(row) -> SettingsOut:
    return SettingsOut(
        reviews_per_day=row.reviews_per_day,
        timezone=row.timezone,
        windows=[NotificationWindow(**w) for w in row.windows],
    )


@router.get("/settings", response_model=SettingsOut)
async def read_settings(db: AsyncSession = Depends(get_session)) -> SettingsOut:
    return _to_out(await get_settings_row(db))


@router.put("/settings", response_model=SettingsOut)
async def write_settings(
    body: SettingsIn, db: AsyncSession = Depends(get_session)
) -> SettingsOut:
    row = await get_settings_row(db)
    row.reviews_per_day = body.reviews_per_day
    row.timezone = body.timezone
    row.windows = [window_to_dict(w) for w in body.windows]
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return _to_out(row)
