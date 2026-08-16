"""Small, process-local launch guardrails for public and paid endpoints.

These controls are deliberately enforced before routing.  They keep oversized
JSON, authentication floods, and provider-call bursts from consuming a database
connection first.  They complement (rather than replace) edge rate limits and a
provider-side hard spend cap.
"""

import asyncio
import hashlib
import re
import time
from collections.abc import AsyncIterator, MutableMapping
from contextlib import asynccontextmanager
from dataclasses import dataclass

from fastapi import HTTPException
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

_WRITE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
_DEFAULT_BODY_BYTES = 2 * 1024 * 1024
_ANSWER_BODY_BYTES = 128 * 1024
_BODY_READ_TIMEOUT_SECONDS = 30
_WRITE_REQUEST_GLOBAL_LIMIT = 64
_WRITE_REQUEST_PER_CREDENTIAL_LIMIT = 4
_CREDENTIAL_HEADERS = (
    "authorization",
    "x-api-key",
    "x-cron-secret",
    "x-founder-claim-token",
)

_AUTH_LIMITS: dict[str, tuple[int, int]] = {
    "/auth/nonce": (120, 60),
    "/auth/apple": (30, 60),
    "/auth/refresh": (120, 60),
    "/auth/apple/notifications": (300, 60),
    "/auth/founder/apple-claim": (10, 60),
}

_PROVIDER_PATHS = (
    re.compile(r"^/cards/[^/]+/sessions$"),
    re.compile(r"^/sessions/[^/]+/(?:answers|reattempt|coaching)$"),
    re.compile(r"^/captures/[^/]+/question$"),
    re.compile(r"^/study-plans/preview(?:/[^/]+/retry)?$"),
    re.compile(r"^/study-plans/[^/]+/items/[^/]+/card-proposals$"),
)

_TIGHT_BODY_PATHS = (
    re.compile(r"^/sessions/[^/]+/(?:draft|answers|reattempt|coaching)$"),
    re.compile(r"^/auth/(?:nonce|apple|refresh|founder/apple-claim)$"),
)


@dataclass
class _Window:
    started_at: float
    count: int


class FixedWindowLimiter:
    """Bounded in-memory limiter; suitable as a last line behind the edge."""

    def __init__(self, *, max_keys: int = 10_000) -> None:
        self._windows: MutableMapping[tuple[str, str], _Window] = {}
        self._lock = asyncio.Lock()
        self._max_keys = max_keys

    async def allow(
        self, *, bucket: str, key: str, limit: int, period_seconds: int
    ) -> tuple[bool, int]:
        now = time.monotonic()
        async with self._lock:
            lookup = (bucket, key)
            window = self._windows.get(lookup)
            if window is None or now - window.started_at >= period_seconds:
                self._windows[lookup] = _Window(started_at=now, count=1)
                self._prune(now, period_seconds)
                return True, period_seconds
            retry_after = max(1, int(period_seconds - (now - window.started_at)) + 1)
            if window.count >= limit:
                return False, retry_after
            window.count += 1
            return True, retry_after

    def _prune(self, now: float, period_seconds: int) -> None:
        if len(self._windows) <= self._max_keys:
            return
        expired = [
            key
            for key, value in self._windows.items()
            if now - value.started_at >= period_seconds
        ]
        for key in expired:
            self._windows.pop(key, None)
        if len(self._windows) > self._max_keys:
            oldest = sorted(
                self._windows, key=lambda key: self._windows[key].started_at
            )[: len(self._windows) - self._max_keys]
            for key in oldest:
                self._windows.pop(key, None)


class ProviderAdmission:
    """Reject provider bursts immediately so inexpensive endpoints stay responsive."""

    def __init__(self, *, global_limit: int = 8, per_credential_limit: int = 2) -> None:
        self._global_limit = global_limit
        self._per_credential_limit = per_credential_limit
        self._global_active = 0
        self._active_by_credential: dict[str, int] = {}
        self._changed = asyncio.Condition()

    def _available(self, credential: str) -> bool:
        return (
            self._global_active < self._global_limit
            and self._active_by_credential.get(credential, 0)
            < self._per_credential_limit
        )

    def _mark_entered(self, credential: str) -> None:
        self._global_active += 1
        self._active_by_credential[credential] = (
            self._active_by_credential.get(credential, 0) + 1
        )

    async def try_enter(self, credential: str) -> bool:
        async with self._changed:
            if not self._available(credential):
                return False
            self._mark_entered(credential)
            return True

    async def wait_enter(self, credential: str) -> None:
        """Queue only bounded internal workers; HTTP requests always use try_enter."""
        async with self._changed:
            await self._changed.wait_for(lambda: self._available(credential))
            self._mark_entered(credential)

    async def leave(self, credential: str) -> None:
        async with self._changed:
            self._global_active = max(0, self._global_active - 1)
            remaining = self._active_by_credential.get(credential, 0) - 1
            if remaining > 0:
                self._active_by_credential[credential] = remaining
            else:
                self._active_by_credential.pop(credential, None)
            self._changed.notify_all()


_paid_provider_admission = ProviderAdmission()


def is_provider_request(method: str, path: str) -> bool:
    return method.upper() == "POST" and any(
        pattern.match(path) for pattern in _PROVIDER_PATHS
    )


@asynccontextmanager
async def provider_slot(
    user_id: object,
    *,
    wait: bool = False,
    admission: ProviderAdmission | None = None,
) -> AsyncIterator[None]:
    """Bound paid work by resolved account identity, never raw credentials."""
    limiter = admission or _paid_provider_admission
    credential = str(user_id)
    entered = False
    try:
        if wait:
            await limiter.wait_enter(credential)
            entered = True
        else:
            entered = await limiter.try_enter(credential)
            if not entered:
                raise HTTPException(
                    status_code=429,
                    detail="provider_busy",
                    headers={"Retry-After": "1"},
                )
        yield
    finally:
        if entered:
            await limiter.leave(credential)


class AbuseProtectionMiddleware:
    """Pre-routing request limits and non-queuing paid-work admission."""

    def __init__(
        self,
        app: ASGIApp,
        *,
        limiter: FixedWindowLimiter | None = None,
        request_admission: ProviderAdmission | None = None,
        default_body_bytes: int = _DEFAULT_BODY_BYTES,
    ) -> None:
        self.app = app
        self.limiter = limiter or FixedWindowLimiter()
        self.request_admission = request_admission or ProviderAdmission(
            global_limit=_WRITE_REQUEST_GLOBAL_LIMIT,
            per_credential_limit=_WRITE_REQUEST_PER_CREDENTIAL_LIMIT,
        )
        self.default_body_bytes = default_body_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path = str(scope.get("path", ""))
        method = str(scope.get("method", "GET")).upper()
        if _has_duplicate_credential_headers(scope):
            await _reject(send, status_code=400, detail="duplicate_credential_header")
            return
        headers = _headers(scope)
        credential = _credential_key(scope, headers)

        auth_limit = _AUTH_LIMITS.get(path)
        if auth_limit is not None:
            limit, period = auth_limit
            allowed, retry_after = await self.limiter.allow(
                bucket=path,
                key=_client_key(scope),
                limit=limit,
                period_seconds=period,
            )
            if not allowed:
                await _reject(
                    send,
                    status_code=429,
                    detail="rate_limited",
                    headers={"Retry-After": str(retry_after)},
                )
                return

        is_write = method in _WRITE_METHODS
        if is_write:
            limit = _body_limit(path, self.default_body_bytes)
            content_length = headers.get("content-length")
            if content_length is not None:
                try:
                    if int(content_length) > limit:
                        await _reject(send, status_code=413, detail="request_too_large")
                        return
                except ValueError:
                    await _reject(send, status_code=400, detail="invalid_content_length")
                    return

        request_admitted = False
        if is_write:
            request_admitted = await self.request_admission.try_enter(credential)
            if not request_admitted:
                await _reject(
                    send,
                    status_code=429,
                    detail="server_busy",
                    headers={"Retry-After": "1"},
                )
                return

        try:
            if not is_write:
                await self.app(scope, receive, send)
                return
            try:
                body, disconnected = await asyncio.wait_for(
                    _read_body(receive, limit), timeout=_BODY_READ_TIMEOUT_SECONDS
                )
            except TimeoutError:
                await _reject(send, status_code=408, detail="request_timeout")
                return
            if body is None:
                await _reject(send, status_code=413, detail="request_too_large")
                return
            if disconnected:
                return
            receive = _replay(body)
            await self.app(scope, receive, send)
        finally:
            if request_admitted:
                await self.request_admission.leave(credential)


def _has_duplicate_credential_headers(scope: Scope) -> bool:
    seen: set[str] = set()
    for raw_name, _value in scope.get("headers", []):
        name = raw_name.decode("latin-1").lower()
        if name not in _CREDENTIAL_HEADERS:
            continue
        if name in seen:
            return True
        seen.add(name)
    return False


def _headers(scope: Scope) -> dict[str, str]:
    return {
        key.decode("latin-1").lower(): value.decode("latin-1")
        for key, value in scope.get("headers", [])
    }


def _client_key(scope: Scope) -> str:
    client = scope.get("client")
    address = str(client[0]) if client else "unknown"
    return hashlib.sha256(address.encode()).hexdigest()


def _credential_key(scope: Scope, headers: dict[str, str]) -> str:
    credential = next(
        (
            headers[name]
            for name in _CREDENTIAL_HEADERS
            if headers.get(name)
        ),
        "",
    )
    material = credential if credential else f"client:{_client_key(scope)}"
    return hashlib.sha256(material.encode()).hexdigest()


def _body_limit(path: str, default: int) -> int:
    if any(pattern.match(path) for pattern in _TIGHT_BODY_PATHS):
        return _ANSWER_BODY_BYTES
    return default


async def _read_body(receive: Receive, limit: int) -> tuple[bytes | None, bool]:
    chunks: list[bytes] = []
    size = 0
    while True:
        message = await receive()
        if message["type"] == "http.disconnect":
            return b"", True
        if message["type"] != "http.request":
            continue
        chunk = message.get("body", b"")
        size += len(chunk)
        if size > limit:
            return None, False
        chunks.append(chunk)
        if not message.get("more_body", False):
            return b"".join(chunks), False


def _replay(body: bytes) -> Receive:
    sent = False

    async def receive() -> Message:
        nonlocal sent
        if sent:
            return {"type": "http.disconnect"}
        sent = True
        return {"type": "http.request", "body": body, "more_body": False}

    return receive


async def _reject(
    send: Send,
    *,
    status_code: int,
    detail: str,
    headers: dict[str, str] | None = None,
) -> None:
    response = JSONResponse(
        status_code=status_code,
        content={"detail": detail},
        headers=headers,
    )
    await response({"type": "http"}, _empty_receive, send)


async def _empty_receive() -> Message:
    return {"type": "http.request", "body": b"", "more_body": False}
