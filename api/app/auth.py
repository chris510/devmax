import secrets
import uuid
from collections.abc import AsyncIterator
from contextvars import ContextVar

from fastapi import Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from sqlmodel.ext.asyncio.session import AsyncSession
from starlette.middleware.base import BaseHTTPMiddleware

from app.config import get_settings
from app.db import get_session
from app.models import FOUNDER_USER_ID
from app.services.authentication import user_for_access_token

# These routes establish authentication rather than consuming it. The founder
# claim is handled separately below because it has its own temporary credential.
PUBLIC_PATHS = {
    "/health",
    "/auth/nonce",
    "/auth/apple",
    "/auth/refresh",
    "/auth/apple/notifications",
}
FOUNDER_CLAIM_PATH = "/auth/founder/apple-claim"
DOCS_PATHS = {"/docs", "/redoc", "/openapi.json"}

_current_user_id: ContextVar[uuid.UUID | None] = ContextVar("devmax_current_user_id", default=None)


def _matches(header: str | None, expected: str) -> bool:
    if not header:
        return False
    return secrets.compare_digest(header, expected)


class AuthMiddleware(BaseHTTPMiddleware):
    """Reject absent credentials before routing; resolve bearer tokens in a dependency."""

    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        if path == FOUNDER_CLAIM_PATH:
            # API_KEY is already embedded in the installed compatibility build,
            # so it must never authorize the permanent founder binding. Empty
            # config disables this path after the one-time migration.
            claim_token = get_settings().founder_claim_token
            ok = bool(claim_token) and _matches(
                request.headers.get("X-Founder-Claim-Token"), claim_token
            )
            if not ok:
                return JSONResponse(status_code=401, content={})
            return await call_next(request)
        if path in PUBLIC_PATHS or path in DOCS_PATHS:
            return await call_next(request)

        settings = get_settings()
        if path.startswith("/internal/"):
            ok = _matches(request.headers.get("X-Cron-Secret"), settings.cron_secret)
            if not ok:
                return JSONResponse(status_code=401, content={})
            return await call_next(request)

        # Migration-only compatibility. The key has exactly one meaning: founder.
        if settings.legacy_api_key_auth_enabled and _matches(
            request.headers.get("X-API-Key"), settings.api_key
        ):
            request.state.user_id = FOUNDER_USER_ID
            return await call_next(request)

        scheme, _, value = request.headers.get("Authorization", "").partition(" ")
        ok = scheme.lower() == "bearer" and bool(value.strip())

        if not ok:
            # No body detail on mismatch.
            return JSONResponse(status_code=401, content={})
        return await call_next(request)


def bearer_token(request: Request) -> str | None:
    scheme, _, value = request.headers.get("Authorization", "").partition(" ")
    return value.strip() if scheme.lower() == "bearer" and value.strip() else None


async def require_user(
    request: Request, db: AsyncSession = Depends(get_session)
) -> AsyncIterator[None]:
    """Resolve one authenticated user and bind it to this async request context."""
    user_id = getattr(request.state, "user_id", None)
    if user_id is None:
        token = bearer_token(request)
        user_id = await user_for_access_token(db, token or "")
    if user_id is None:
        raise HTTPException(status_code=401, detail="unauthorized")

    request.state.user_id = user_id
    reset = _current_user_id.set(user_id)
    try:
        yield
    finally:
        _current_user_id.reset(reset)


def current_user_id() -> uuid.UUID:
    user_id = _current_user_id.get()
    if user_id is None:  # pragma: no cover - every client router installs require_user
        raise RuntimeError("authenticated user context is missing")
    return user_id
