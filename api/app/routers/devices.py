from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy import func
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.auth import current_user_id
from app.db import get_session
from app.models import DeviceToken, User
from app.schemas import DeviceTokenIn

router = APIRouter(tags=["devices"])

MAX_DEVICE_TOKENS_PER_USER = 10


@router.post("/device-tokens", status_code=204)
async def upsert_device_token(
    body: DeviceTokenIn, db: AsyncSession = Depends(get_session)
) -> Response:
    user_id = current_user_id()
    # Serialize the per-account count. Existing-token registration remains
    # idempotent at the cap; moving a token to another signed-in account consumes
    # one slot on the destination account.
    user = await db.get(User, user_id, with_for_update=True)
    if user is None:  # pragma: no cover - authentication resolved this user
        raise HTTPException(status_code=401, detail="unauthorized")
    existing = await db.get(
        DeviceToken,
        body.token,
        with_for_update=True,
        populate_existing=True,
    )
    if existing is None or existing.user_id != user_id:
        count = (
            await db.exec(
                select(func.count(DeviceToken.token)).where(
                    DeviceToken.user_id == user_id
                )
            )
        ).one()
        if count >= MAX_DEVICE_TOKENS_PER_USER:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "device_token_limit",
                    "limit": MAX_DEVICE_TOKENS_PER_USER,
                },
            )
    if existing is None:
        db.add(DeviceToken(token=body.token, user_id=user_id, kind=body.kind))
    else:
        # The client re-registers on every launch, so this is the common path.
        # `created_at` is deliberately left alone. It records when the token was
        # first seen, and the sandbox/production split is carried by `kind`,
        # which must follow the build that just registered.
        existing.kind = body.kind
        existing.user_id = user_id
    await db.commit()
    return Response(status_code=204)
