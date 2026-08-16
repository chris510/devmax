"""Sign in with Apple verification and opaque Devmax session tokens.

Apple credentials establish identity once. Devmax's own short-lived access token
then authenticates API calls; rotating refresh tokens extend the device session.
Only SHA-256 hashes of Devmax tokens are stored.
"""

import asyncio
import hashlib
import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import jwt
from cryptography.fernet import Fernet, InvalidToken
from jwt import PyJWKClient
from sqlalchemy import and_, or_
from sqlalchemy.exc import IntegrityError
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.config import Settings as AppConfig
from app.models import (
    FOUNDER_USER_ID,
    USER_ACTIVE,
    AppleIdentity,
    AuthNonce,
    AuthSession,
    Settings,
    User,
)

APPLE_ISSUER = "https://appleid.apple.com"
APPLE_JWKS_URL = "https://appleid.apple.com/auth/keys"
APPLE_TOKEN_URL = "https://appleid.apple.com/auth/token"
APPLE_REVOKE_URL = "https://appleid.apple.com/auth/revoke"
NONCE_TTL = timedelta(minutes=10)
USED_NONCE_RETENTION = timedelta(minutes=5)
AUTH_CLEANUP_BATCH_SIZE = 100
APPLE_JWK_CLIENT = PyJWKClient(
    APPLE_JWKS_URL,
    cache_jwk_set=True,
    lifespan=3600,
    timeout=10,
)


class AuthenticationError(Exception):
    """A deliberately detail-free authentication failure."""


class AuthenticationUnavailable(Exception):
    """Sign in with Apple is not configured or reachable."""


@dataclass(frozen=True)
class AppleClaims:
    subject: str
    email: str | None


@dataclass(frozen=True)
class AppleCodeExchange:
    subject: str
    refresh_token: str | None


@dataclass(frozen=True)
class AppleAccountEvent:
    jti: str
    event_type: str
    subject: str
    occurred_at: datetime


@dataclass(frozen=True)
class TokenPair:
    access_token: str
    refresh_token: str
    access_expires_at: datetime
    refresh_expires_at: datetime


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def apple_nonce(raw_nonce: str) -> str:
    """The SHA-256 value sent to Apple and returned in the identity token."""
    return token_hash(raw_nonce)


def _now() -> datetime:
    return datetime.now(UTC)


def _as_utc(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def _configured(config: AppConfig) -> bool:
    return all(
        (
            config.apple_client_id,
            config.apple_team_id,
            config.apple_key_id,
            config.apple_private_key,
            config.auth_encryption_key,
        )
    )


async def issue_nonce(db: AsyncSession) -> str:
    await cleanup_auth_rows(db)
    nonce = secrets.token_urlsafe(32)
    db.add(AuthNonce(nonce_hash=token_hash(nonce), expires_at=_now() + NONCE_TTL))
    await db.commit()
    return nonce


async def _consume_nonce(db: AsyncSession, nonce: str) -> None:
    row = (
        await db.exec(
            select(AuthNonce).where(AuthNonce.nonce_hash == token_hash(nonce)).with_for_update()
        )
    ).first()
    if row is None or row.used_at is not None or _as_utc(row.expires_at) <= _now():
        raise AuthenticationError
    row.used_at = _now()
    db.add(row)
    await db.flush()


async def verify_apple_identity_token(
    identity_token: str, nonce: str, config: AppConfig
) -> AppleClaims:
    if not _configured(config):
        raise AuthenticationUnavailable

    def decode() -> dict[str, Any]:
        key = APPLE_JWK_CLIENT.get_signing_key_from_jwt(identity_token)
        return jwt.decode(
            identity_token,
            key.key,
            algorithms=["RS256"],
            audience=config.apple_client_id,
            issuer=APPLE_ISSUER,
            options={"require": ["exp", "iat", "iss", "aud", "sub", "nonce"]},
        )

    try:
        claims = await asyncio.to_thread(decode)
    except (jwt.PyJWTError, OSError) as exc:
        raise AuthenticationError from exc

    claim_nonce = claims.get("nonce")
    if not isinstance(claim_nonce, str) or not secrets.compare_digest(
        claim_nonce, apple_nonce(nonce)
    ):
        raise AuthenticationError
    subject = claims.get("sub")
    if not isinstance(subject, str) or not subject:
        raise AuthenticationError
    email = claims.get("email")
    return AppleClaims(subject=subject, email=email if isinstance(email, str) else None)


async def verify_apple_server_notification(
    payload: str, config: AppConfig
) -> AppleAccountEvent:
    """Verify Apple's signed server-to-server account-change payload."""
    if not _configured(config):
        raise AuthenticationUnavailable

    def decode() -> dict[str, Any]:
        key = APPLE_JWK_CLIENT.get_signing_key_from_jwt(payload)
        return jwt.decode(
            payload,
            key.key,
            algorithms=["RS256"],
            audience=config.apple_client_id,
            issuer=APPLE_ISSUER,
            options={"require": ["iss", "aud", "iat", "jti", "events"]},
        )

    try:
        claims = await asyncio.to_thread(decode)
    except (jwt.PyJWTError, OSError) as exc:
        raise AuthenticationError from exc
    events = claims.get("events")
    jti = claims.get("jti")
    if not isinstance(events, dict):
        raise AuthenticationError
    event_type = events.get("type")
    subject = events.get("sub")
    event_time = events.get("event_time")
    if (
        not isinstance(jti, str)
        or not jti
        or not isinstance(event_type, str)
        or not isinstance(subject, str)
        or not subject
        or not isinstance(event_time, int)
    ):
        raise AuthenticationError
    if event_type not in {
        "email-disabled",
        "email-enabled",
        "consent-revoked",
        "account-deleted",
    }:
        raise AuthenticationError
    try:
        occurred_at = datetime.fromtimestamp(event_time, UTC)
    except (OverflowError, OSError, ValueError) as exc:
        raise AuthenticationError from exc
    return AppleAccountEvent(
        jti=jti,
        event_type=event_type,
        subject=subject,
        occurred_at=occurred_at,
    )


def _apple_client_secret(config: AppConfig) -> str:
    now = _now()
    return jwt.encode(
        {
            "iss": config.apple_team_id,
            "iat": int(now.timestamp()),
            "exp": int((now + timedelta(minutes=10)).timestamp()),
            "aud": APPLE_ISSUER,
            "sub": config.apple_client_id,
        },
        config.apple_private_key,
        algorithm="ES256",
        headers={"kid": config.apple_key_id},
    )


async def exchange_apple_code(
    code: str, nonce: str, config: AppConfig
) -> AppleCodeExchange:
    if not _configured(config):
        raise AuthenticationUnavailable
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.post(
                APPLE_TOKEN_URL,
                data={
                    "client_id": config.apple_client_id,
                    "client_secret": _apple_client_secret(config),
                    "code": code,
                    "grant_type": "authorization_code",
                },
                headers={"Accept": "application/json"},
            )
    except (httpx.HTTPError, jwt.PyJWTError, ValueError) as exc:
        raise AuthenticationUnavailable from exc
    if response.status_code != 200:
        raise AuthenticationError
    try:
        payload = response.json()
    except ValueError as exc:
        raise AuthenticationUnavailable from exc
    if not isinstance(payload, dict):
        raise AuthenticationUnavailable
    identity_token = payload.get("id_token")
    if not isinstance(identity_token, str) or not identity_token:
        raise AuthenticationError
    claims = await verify_apple_identity_token(identity_token, nonce, config)
    refresh = payload.get("refresh_token")
    return AppleCodeExchange(
        subject=claims.subject,
        refresh_token=refresh if isinstance(refresh, str) and refresh else None,
    )


async def revoke_apple_authorization(refresh_token: str, config: AppConfig) -> None:
    if not _configured(config):
        raise AuthenticationUnavailable
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.post(
                APPLE_REVOKE_URL,
                data={
                    "client_id": config.apple_client_id,
                    "client_secret": _apple_client_secret(config),
                    "token": refresh_token,
                    "token_type_hint": "refresh_token",
                },
            )
    except (httpx.HTTPError, jwt.PyJWTError, ValueError) as exc:
        raise AuthenticationUnavailable from exc
    if response.status_code != 200:
        raise AuthenticationUnavailable


def _encrypt_apple_token(token: str, config: AppConfig) -> str:
    try:
        return (
            Fernet(config.auth_encryption_key.encode("ascii"))
            .encrypt(token.encode("utf-8"))
            .decode("ascii")
        )
    except (ValueError, TypeError) as exc:
        raise AuthenticationUnavailable from exc


def decrypt_apple_token(token: str, config: AppConfig) -> str:
    try:
        return (
            Fernet(config.auth_encryption_key.encode("ascii"))
            .decrypt(token.encode("ascii"))
            .decode("utf-8")
        )
    except (InvalidToken, ValueError, TypeError) as exc:
        raise AuthenticationUnavailable from exc


def _apply_apple_authorization(
    identity: AppleIdentity,
    *,
    claims: AppleClaims,
    display_name: str | None,
    exchange: AppleCodeExchange,
    config: AppConfig,
) -> None:
    if claims.email:
        identity.email = claims.email
    if display_name and display_name.strip():
        identity.display_name = display_name.strip()[:200]
    if exchange.refresh_token:
        identity.apple_refresh_token = _encrypt_apple_token(
            exchange.refresh_token, config
        )
    identity.authorization_revoked_at = None
    identity.last_apple_authorized_at = _now().replace(microsecond=0)
    identity.updated_at = _now()


async def issue_session(
    db: AsyncSession,
    user_id: uuid.UUID,
    config: AppConfig,
    *,
    family_id: uuid.UUID | None = None,
    rotated_from_id: uuid.UUID | None = None,
) -> TokenPair:
    await cleanup_auth_rows(db)
    access = secrets.token_urlsafe(32)
    refresh = secrets.token_urlsafe(48)
    now = _now()
    access_expires = now + timedelta(minutes=config.access_token_ttl_minutes)
    refresh_expires = now + timedelta(days=config.refresh_token_ttl_days)
    db.add(
        AuthSession(
            user_id=user_id,
            family_id=family_id or uuid.uuid4(),
            rotated_from_id=rotated_from_id,
            access_token_hash=token_hash(access),
            refresh_token_hash=token_hash(refresh),
            access_expires_at=access_expires,
            refresh_expires_at=refresh_expires,
        )
    )
    await db.flush()
    return TokenPair(access, refresh, access_expires, refresh_expires)


async def sign_in_with_apple(
    db: AsyncSession,
    *,
    identity_token: str,
    authorization_code: str,
    nonce: str,
    display_name: str | None,
    config: AppConfig,
) -> tuple[User, TokenPair]:
    claims = await verify_apple_identity_token(identity_token, nonce, config)
    await _consume_nonce(db, nonce)
    exchange = await exchange_apple_code(authorization_code, nonce, config)
    if not secrets.compare_digest(exchange.subject, claims.subject):
        raise AuthenticationError

    try:
        identity = (
            await db.exec(
                select(AppleIdentity)
                .where(AppleIdentity.subject == claims.subject)
                .with_for_update()
            )
        ).first()
        if identity is None:
            # While the one-time founder claim is open, this public route may
            # resume an existing identity but cannot create any account. Removing
            # the temporary token opens signup without coupling it forever to the
            # founder account's lifecycle.
            if config.founder_claim_token or exchange.refresh_token is None:
                raise AuthenticationError
            user = User()
            db.add(user)
            await db.flush()
            identity = AppleIdentity(user_id=user.id, subject=claims.subject)
            db.add(identity)
            db.add(Settings(user_id=user.id))
        else:
            user = await db.get(User, identity.user_id)
            if user is None or user.status != USER_ACTIVE:
                raise AuthenticationError

        _apply_apple_authorization(
            identity,
            claims=claims,
            display_name=display_name,
            exchange=exchange,
            config=config,
        )
        db.add(identity)

        pair = await issue_session(db, user.id, config)
        await db.commit()
        return user, pair
    except IntegrityError as exc:
        # The founder claim and public sign-up can observe each other's state
        # between reads. Unique identity indexes remain authoritative; the loser
        # gets a controlled, detail-free authentication failure instead of 500.
        await db.rollback()
        raise AuthenticationError from exc


async def claim_founder_with_apple(
    db: AsyncSession,
    *,
    identity_token: str,
    authorization_code: str,
    nonce: str,
    display_name: str | None,
    config: AppConfig,
) -> tuple[User, TokenPair]:
    """Bind verified Apple identity to the fixed founder without moving data.

    The temporary claim secret is authenticated by middleware. This transaction
    supplies the permanent latch: the founder row is locked, the Apple subject
    must be unowned (or already owned by this founder), and both ownership
    dimensions are also protected by unique database indexes. A fresh Apple
    proof for the same subject is intentionally idempotent so a lost HTTP
    response cannot strand the migration; replaying the same nonce still fails.
    """

    claims = await verify_apple_identity_token(identity_token, nonce, config)
    await _consume_nonce(db, nonce)

    founder = (
        await db.exec(
            select(User).where(User.id == FOUNDER_USER_ID).with_for_update()
        )
    ).first()
    if founder is None or founder.status != USER_ACTIVE or not founder.is_founder:
        raise AuthenticationError

    founder_identity = (
        await db.exec(
            select(AppleIdentity)
            .where(AppleIdentity.user_id == FOUNDER_USER_ID)
            .with_for_update()
        )
    ).first()
    subject_identity = (
        await db.exec(
            select(AppleIdentity)
            .where(AppleIdentity.subject == claims.subject)
            .with_for_update()
        )
    ).first()

    if founder_identity is not None:
        if founder_identity.subject != claims.subject:
            raise AuthenticationError
        if subject_identity is None or subject_identity.id != founder_identity.id:
            raise AuthenticationError
        identity = founder_identity
    else:
        if subject_identity is not None:
            raise AuthenticationError
        identity = AppleIdentity(user_id=FOUNDER_USER_ID, subject=claims.subject)

    # Validate the single-use authorization code before either creating the
    # permanent identity or minting Devmax credentials.
    exchange = await exchange_apple_code(authorization_code, nonce, config)
    if not secrets.compare_digest(exchange.subject, claims.subject):
        raise AuthenticationError
    if founder_identity is None and exchange.refresh_token is None:
        raise AuthenticationError
    _apply_apple_authorization(
        identity,
        claims=claims,
        display_name=display_name,
        exchange=exchange,
        config=config,
    )
    db.add(identity)

    try:
        pair = await issue_session(db, FOUNDER_USER_ID, config)
        await db.commit()
    except IntegrityError as exc:
        # The unique subject/user indexes are the final concurrency arbiter for
        # engines where SELECT FOR UPDATE is unavailable or another identity is
        # inserted between the checks and flush.
        await db.rollback()
        raise AuthenticationError from exc
    return founder, pair


async def user_for_access_token(db: AsyncSession, token: str) -> uuid.UUID | None:
    return (
        await db.exec(
            select(AuthSession.user_id)
            .join(User, User.id == AuthSession.user_id)
            .where(
                AuthSession.access_token_hash == token_hash(token),
                col(AuthSession.revoked_at).is_(None),
                AuthSession.access_expires_at > _now(),
                User.status == USER_ACTIVE,
            )
        )
    ).first()


async def rotate_refresh_token(
    db: AsyncSession, refresh_token: str, config: AppConfig
) -> TokenPair:
    current = (
        await db.exec(
            select(AuthSession)
            .where(AuthSession.refresh_token_hash == token_hash(refresh_token))
            .with_for_update()
        )
    ).first()
    if current is None:
        raise AuthenticationError

    now = _now()
    if current.revoked_at is not None:
        # Reuse of a rotated token is evidence that the token family escaped the
        # device. Revoke the replacement family, not only the replayed row.
        rows = (
            await db.exec(select(AuthSession).where(AuthSession.family_id == current.family_id))
        ).all()
        for row in rows:
            if row.revoked_at is None:
                row.revoked_at = now
                row.updated_at = now
                db.add(row)
        await db.commit()
        raise AuthenticationError
    if _as_utc(current.refresh_expires_at) <= now:
        current.revoked_at = now
        db.add(current)
        await db.commit()
        raise AuthenticationError

    current.revoked_at = now
    current.updated_at = now
    db.add(current)
    pair = await issue_session(
        db,
        current.user_id,
        config,
        family_id=current.family_id,
        rotated_from_id=current.id,
    )
    await db.commit()
    return pair


async def revoke_access_token(db: AsyncSession, access_token: str) -> None:
    row = (
        await db.exec(
            select(AuthSession).where(
                AuthSession.access_token_hash == token_hash(access_token),
                col(AuthSession.revoked_at).is_(None),
            )
        )
    ).first()
    if row is not None:
        row.revoked_at = _now()
        row.updated_at = _now()
        db.add(row)
        await db.commit()


async def cleanup_auth_rows(
    db: AsyncSession,
    *,
    now: datetime | None = None,
    batch_size: int = AUTH_CLEANUP_BATCH_SIZE,
) -> tuple[int, int]:
    """Delete bounded, no-longer-security-relevant authentication rows.

    Used nonces remain briefly for operational inspection; expired nonces can
    go immediately. A revoked/rotated session is retained until *both* of its
    credentials have expired so refresh-token replay can still revoke a live
    replacement family during the original token's validity window.

    Cleanup is opportunistic on nonce and session issuance. Each table has an
    independent cap so a nonce flood cannot starve expired-session retention.
    """

    if batch_size < 1:
        return 0, 0
    cutoff = now or _now()
    used_cutoff = cutoff - USED_NONCE_RETENTION
    nonces = (
        await db.exec(
            select(AuthNonce)
            .where(
                or_(
                    AuthNonce.expires_at <= cutoff,
                    col(AuthNonce.used_at) <= used_cutoff,
                )
            )
            .order_by(AuthNonce.expires_at, AuthNonce.id)
            .limit(batch_size)
        )
    ).all()
    for nonce in nonces:
        await db.delete(nonce)

    sessions = (
        await db.exec(
            select(AuthSession)
            .where(
                and_(
                    AuthSession.access_expires_at <= cutoff,
                    AuthSession.refresh_expires_at <= cutoff,
                )
            )
            .order_by(AuthSession.refresh_expires_at, AuthSession.id)
            .limit(batch_size)
        )
    ).all()
    for session in sessions:
        await db.delete(session)
    await db.flush()
    return len(nonces), len(sessions)
