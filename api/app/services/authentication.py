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
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.config import Settings as AppConfig
from app.models import (
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


async def exchange_apple_code(code: str, config: AppConfig) -> str | None:
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
    refresh = payload.get("refresh_token")
    return refresh if isinstance(refresh, str) and refresh else None


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


async def issue_session(
    db: AsyncSession,
    user_id: uuid.UUID,
    config: AppConfig,
    *,
    family_id: uuid.UUID | None = None,
    rotated_from_id: uuid.UUID | None = None,
) -> TokenPair:
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
    apple_refresh = await exchange_apple_code(authorization_code, config)

    identity = (
        await db.exec(select(AppleIdentity).where(AppleIdentity.subject == claims.subject))
    ).first()
    if identity is None:
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

    if claims.email:
        identity.email = claims.email
    if display_name and display_name.strip():
        identity.display_name = display_name.strip()[:200]
    if apple_refresh:
        identity.apple_refresh_token = _encrypt_apple_token(apple_refresh, config)
    identity.updated_at = _now()
    db.add(identity)

    pair = await issue_session(db, user.id, config)
    await db.commit()
    return user, pair


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
