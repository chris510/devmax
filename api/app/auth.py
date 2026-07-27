import secrets

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from app.config import get_settings

# Health is the only unauthenticated route (the platform health check hits it).
PUBLIC_PATHS = {"/health"}
DOCS_PATHS = {"/docs", "/redoc", "/openapi.json"}


def _matches(header: str | None, expected: str) -> bool:
    if not header:
        return False
    return secrets.compare_digest(header, expected)


class AuthMiddleware(BaseHTTPMiddleware):
    """Two independent shared secrets. No JWT, no user table — deliberate."""

    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        if path in PUBLIC_PATHS or path in DOCS_PATHS:
            return await call_next(request)

        settings = get_settings()
        if path.startswith("/internal/"):
            ok = _matches(request.headers.get("X-Cron-Secret"), settings.cron_secret)
        else:
            ok = _matches(request.headers.get("X-API-Key"), settings.api_key)

        if not ok:
            # No body detail on mismatch.
            return JSONResponse(status_code=401, content={})
        return await call_next(request)
