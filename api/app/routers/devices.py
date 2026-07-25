from fastapi import APIRouter, Depends, Response
from sqlmodel.ext.asyncio.session import AsyncSession

from app.db import get_session
from app.models import DeviceToken
from app.schemas import DeviceTokenIn

router = APIRouter(tags=["devices"])


@router.post("/device-tokens", status_code=204)
async def upsert_device_token(
    body: DeviceTokenIn, db: AsyncSession = Depends(get_session)
) -> Response:
    existing = await db.get(DeviceToken, body.token)
    if existing is None:
        db.add(DeviceToken(token=body.token, kind=body.kind))
        await db.commit()
    return Response(status_code=204)
