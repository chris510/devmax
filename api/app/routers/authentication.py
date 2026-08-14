from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.auth import bearer_token, current_user_id, require_user
from app.config import get_settings
from app.db import get_session
from app.models import (
    AIConsentEvent,
    AppleIdentity,
    AuthSession,
    Card,
    LLMUsage,
    MaterialSource,
    Session,
    Settings,
    StudyPlan,
    User,
)
from app.schemas import (
    AccountExport,
    AIConsentIn,
    AIConsentOut,
    AppleServerNotificationIn,
    AppleSignInIn,
    AuthNonceOut,
    CurrentUserOut,
    RefreshTokenIn,
    TokenOut,
)
from app.services import ai_consent, authentication

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
        apple_user_identifier=identity.subject if identity else "",
        ai_consent_status=user.ai_consent_status,
        ai_consent_version=user.ai_consent_version,
        ai_consent_updated_at=user.ai_consent_updated_at,
        ai_processing_allowed=ai_consent.processing_allowed(user),
        ai_consent_prompt_required=ai_consent.prompt_required(user),
    )


@router.put(
    "/ai-consent", response_model=AIConsentOut, dependencies=[Depends(require_user)]
)
async def update_ai_consent(
    body: AIConsentIn, db: AsyncSession = Depends(get_session)
) -> AIConsentOut:
    user, changed_at = await ai_consent.record(
        db, current_user_id(), body.action, body.policy_version
    )
    return AIConsentOut(
        provider=ai_consent.PROVIDER,
        policy_version=ai_consent.POLICY_VERSION,
        status=user.ai_consent_status,
        updated_at=changed_at,
        processing_allowed=ai_consent.processing_allowed(user),
        prompt_required=ai_consent.prompt_required(user),
    )


@router.post("/apple/notifications", status_code=204)
async def apple_notifications(
    body: AppleServerNotificationIn, db: AsyncSession = Depends(get_session)
) -> Response:
    """Invalidate access on verified Apple account-change events without deleting study data."""
    try:
        event = await authentication.verify_apple_server_notification(
            body.payload, get_settings()
        )
    except authentication.AuthenticationUnavailable as exc:
        raise HTTPException(status_code=503, detail="notification_unavailable") from exc
    except authentication.AuthenticationError as exc:
        raise HTTPException(status_code=401, detail="unauthorized") from exc

    identity = (
        await db.exec(
            select(AppleIdentity)
            .where(AppleIdentity.subject == event.subject)
            .with_for_update()
        )
    ).first()
    if identity is None:
        return Response(status_code=204)
    now = datetime.now(UTC)
    # Apple's notifications are retryable. An event generated before the most
    # recent successful Apple authorization must not revoke the fresh session if
    # an old delivery is replayed afterward.
    if (
        identity.last_apple_event_at is not None
        and event.occurred_at <= identity.last_apple_event_at
    ):
        return Response(status_code=204)
    if event.event_type in {"consent-revoked", "account-deleted"}:
        identity.apple_refresh_token = None
        identity.authorization_revoked_at = event.occurred_at
        sessions = (
            await db.exec(
                select(AuthSession).where(
                    AuthSession.user_id == identity.user_id,
                    col(AuthSession.revoked_at).is_(None),
                )
            )
        ).all()
        for session in sessions:
            session.revoked_at = now
            session.updated_at = now
            db.add(session)
    elif event.event_type == "email-disabled":
        identity.email = None
    identity.last_apple_event_at = event.occurred_at
    identity.updated_at = now
    db.add(identity)
    await db.commit()
    return Response(status_code=204)


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
    consent_events = (
        await db.exec(
            select(AIConsentEvent)
            .where(AIConsentEvent.user_id == user_id)
            .order_by(AIConsentEvent.created_at)
        )
    ).all()
    model_usage = (
        await db.exec(
            select(LLMUsage)
            .where(LLMUsage.user_id == user_id)
            .order_by(LLMUsage.created_at, LLMUsage.id)
        )
    ).all()
    return AccountExport(
        exported_at=datetime.now(UTC),
        account=user.model_dump() if user else {},
        settings=settings.model_dump() if settings else {},
        sources=[row.model_dump() for row in sources],
        cards=[row.model_dump() for row in cards],
        sessions=[row.model_dump() for row in sessions],
        study_plans=[row.model_dump() for row in plans],
        ai_consent_events=[row.model_dump() for row in consent_events],
        llm_usage=[row.model_dump() for row in model_usage],
    )


@router.delete("/account", status_code=204, dependencies=[Depends(require_user)])
async def delete_account(db: AsyncSession = Depends(get_session)) -> Response:
    # Read the Apple credential without locking the User.  The network revocation
    # can be slow, and holding a User row lock across it would block independent
    # provider-audit FK inserts and can deadlock a provider transaction.
    user = await db.get(User, current_user_id())
    if user is None:
        return Response(status_code=204)
    identity = (
        await db.exec(select(AppleIdentity).where(AppleIdentity.user_id == user.id))
    ).first()
    encrypted_apple_token = identity.apple_refresh_token if identity else None
    # End the read transaction before external I/O.  No deletion state has been
    # changed, so an Apple failure still leaves the account wholly intact.
    await db.rollback()
    if encrypted_apple_token:
        try:
            token = authentication.decrypt_apple_token(encrypted_apple_token, get_settings())
            await authentication.revoke_apple_authorization(token, get_settings())
        except authentication.AuthenticationUnavailable as exc:
            raise HTTPException(status_code=503, detail="deletion_unavailable") from exc
    # Provider authorization, consent changes, result finalization, and deletion
    # share this per-user boundary.  Re-read after the external Apple revocation:
    # another deletion may have completed while no database lock was held.
    user = await ai_consent.lock_user_boundary(db, current_user_id())
    if user is None:
        return Response(status_code=204)
    await db.delete(user)
    await db.commit()
    return Response(status_code=204)


@router.post("/logout", status_code=204, dependencies=[Depends(require_user)])
async def logout(request: Request, db: AsyncSession = Depends(get_session)) -> Response:
    token = bearer_token(request)
    if token:
        await authentication.revoke_access_token(db, token)
    return Response(status_code=204)
