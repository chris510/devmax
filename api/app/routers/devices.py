from fastapi import APIRouter, Depends, Response
from sqlmodel.ext.asyncio.session import AsyncSession

from app.auth import current_user_id
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
        db.add(DeviceToken(token=body.token, user_id=current_user_id(), kind=body.kind))
    else:
        # The client re-registers on every launch, so this is the common path.
        # `created_at` is deliberately left alone — it records when the token was
        # first seen, and the sandbox/production split is carried by `kind`,
        # which must follow the build that just registered.
        existing.kind = body.kind
        existing.user_id = current_user_id()
    await db.commit()
    return Response(status_code=204)
