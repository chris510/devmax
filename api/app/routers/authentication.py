from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.auth import bearer_token, current_user_id, require_user
from app.config import get_settings
from app.db import get_session
from app.models import AppleIdentity, Card, MaterialSource, Session, Settings, StudyPlan, User
from app.schemas import (
    AccountExport,
    AppleSignInIn,
    AuthNonceOut,
    CurrentUserOut,
    RefreshTokenIn,
    TokenOut,
)
from app.services import authentication

router = APIRouter(prefix="/auth", tags=["authentication"])


def _out(pair: authentication.TokenPair) -> TokenOut:
    return TokenOut(
        access_token=pair.access_token,
        refresh_token=pair.refresh_token,
        access_expires_at=pair.access_expires_at,
        refresh_expires_at=pair.refresh_expires_at,
    )


@router.post("/nonce", response_model=AuthNonceOut)
async def nonce(db: AsyncSession = Depends(get_session)) -> AuthNonceOut:
    return AuthNonceOut(nonce=await authentication.issue_nonce(db))


@router.post("/apple", response_model=TokenOut)
async def apple_sign_in(body: AppleSignInIn, db: AsyncSession = Depends(get_session)) -> TokenOut:
    try:
        _, pair = await authentication.sign_in_with_apple(
            db,
            identity_token=body.identity_token,
            authorization_code=body.authorization_code,
            nonce=body.nonce,
            display_name=body.display_name,
            config=get_settings(),
        )
    except authentication.AuthenticationUnavailable as exc:
        await db.rollback()
        raise HTTPException(status_code=503, detail="sign_in_unavailable") from exc
    except authentication.AuthenticationError as exc:
        await db.rollback()
        raise HTTPException(status_code=401, detail="unauthorized") from exc
    return _out(pair)


@router.post("/founder/apple-claim", response_model=TokenOut)
async def founder_apple_claim(
    body: AppleSignInIn, db: AsyncSession = Depends(get_session)
) -> TokenOut:
    """One-time founder migration; middleware requires its dedicated secret."""
    try:
        _, pair = await authentication.claim_founder_with_apple(
            db,
            identity_token=body.identity_token,
            authorization_code=body.authorization_code,
            nonce=body.nonce,
            display_name=body.display_name,
            config=get_settings(),
        )
    except authentication.AuthenticationUnavailable as exc:
        await db.rollback()
        raise HTTPException(status_code=503, detail="sign_in_unavailable") from exc
    except authentication.AuthenticationError as exc:
        await db.rollback()
        raise HTTPException(status_code=401, detail="unauthorized") from exc
    return _out(pair)


@router.post("/refresh", response_model=TokenOut)
async def refresh(body: RefreshTokenIn, db: AsyncSession = Depends(get_session)) -> TokenOut:
    try:
        return _out(
            await authentication.rotate_refresh_token(db, body.refresh_token, get_settings())
        )
    except authentication.AuthenticationError as exc:
        raise HTTPException(status_code=401, detail="unauthorized") from exc


@router.get("/me", response_model=CurrentUserOut, dependencies=[Depends(require_user)])
async def me(db: AsyncSession = Depends(get_session)) -> CurrentUserOut:
    user = await db.get(User, current_user_id())
    identity = (
        await db.exec(select(AppleIdentity).where(AppleIdentity.user_id == current_user_id()))
    ).first()
    if user is None:
        raise HTTPException(status_code=401, detail="unauthorized")
    return CurrentUserOut(
        id=user.id,
        onboarding_completed=user.onboarding_completed,
        is_founder=user.is_founder,
        display_name=identity.display_name if identity and identity.display_name else "",
        email=identity.email if identity and identity.email else "",
    )


@router.post(
    "/onboarding/complete", response_model=CurrentUserOut, dependencies=[Depends(require_user)]
)
async def complete_onboarding(db: AsyncSession = Depends(get_session)) -> CurrentUserOut:
    user = await db.get(User, current_user_id())
    if user is None:
        raise HTTPException(status_code=401, detail="unauthorized")
    user.onboarding_completed = True
    user.updated_at = datetime.now(UTC)
    db.add(user)
    await db.commit()
    return await me(db)


@router.get("/export", response_model=AccountExport, dependencies=[Depends(require_user)])
async def export_account(db: AsyncSession = Depends(get_session)) -> AccountExport:
    user_id = current_user_id()
    user = await db.get(User, user_id)
    settings = (await db.exec(select(Settings).where(Settings.user_id == user_id))).first()
    sources = (await db.exec(select(MaterialSource).where(MaterialSource.user_id == user_id))).all()
    cards = (await db.exec(select(Card).where(Card.user_id == user_id))).all()
    sessions = (
        await db.exec(
            select(Session).join(Card, Card.id == Session.card_id).where(Card.user_id == user_id)
        )
    ).all()
    plans = (await db.exec(select(StudyPlan).where(StudyPlan.user_id == user_id))).all()
    return AccountExport(
        exported_at=datetime.now(UTC),
        account=user.model_dump() if user else {},
        settings=settings.model_dump() if settings else {},
        sources=[row.model_dump() for row in sources],
        cards=[row.model_dump() for row in cards],
        sessions=[row.model_dump() for row in sessions],
        study_plans=[row.model_dump() for row in plans],
    )


@router.delete("/account", status_code=204, dependencies=[Depends(require_user)])
async def delete_account(db: AsyncSession = Depends(get_session)) -> Response:
    user = await db.get(User, current_user_id())
    if user is None:
        return Response(status_code=204)
    identity = (
        await db.exec(select(AppleIdentity).where(AppleIdentity.user_id == user.id))
    ).first()
    if identity and identity.apple_refresh_token:
        try:
            token = authentication.decrypt_apple_token(identity.apple_refresh_token, get_settings())
            await authentication.revoke_apple_authorization(token, get_settings())
        except authentication.AuthenticationUnavailable as exc:
            raise HTTPException(status_code=503, detail="deletion_unavailable") from exc
    await db.delete(user)
    await db.commit()
    return Response(status_code=204)


@router.post("/logout", status_code=204, dependencies=[Depends(require_user)])
async def logout(request: Request, db: AsyncSession = Depends(get_session)) -> Response:
    token = bearer_token(request)
    if token:
        await authentication.revoke_access_token(db, token)
    return Response(status_code=204)
