import asyncio
import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress

from fastapi import Depends, FastAPI, Request
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.auth import AuthMiddleware, require_user
from app.config import get_settings
from app.db import session_factory
from app.routers import (
    authentication,
    captures,
    cards,
    devices,
    internal,
    materials,
    sessions,
    settings,
    study_plan,
)
from app.services.llm import LLMError
from app.services.review_poller import run_review_poller

_settings = get_settings()
logging.basicConfig(level=_settings.log_level)


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    poller: asyncio.Task[None] | None = None
    import_jobs = await materials.schedule_pending_imports()
    if _settings.review_poller_enabled:
        port = os.environ.get("PORT", "8080")
        poller = asyncio.create_task(
            run_review_poller(
                f"http://127.0.0.1:{port}",
                _settings.cron_secret,
                interval_seconds=_settings.review_poll_interval_seconds,
            ),
            name="review-poller",
        )
        logging.getLogger(__name__).info(
            "review poller enabled interval_seconds=%s",
            _settings.review_poll_interval_seconds,
        )
    try:
        yield
    finally:
        for job in import_jobs:
            job.cancel()
        for job in import_jobs:
            with suppress(asyncio.CancelledError):
                await job
        if poller is not None:
            poller.cancel()
            with suppress(asyncio.CancelledError):
                await poller


app = FastAPI(
    title="Unprompted API",
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
    lifespan=lifespan,
)
app.add_middleware(AuthMiddleware)

# Not fatal: deploying before the Apple credentials exist is the recommended order.
# But send_push() returns 0 silently in that state, so without this the only symptom
# is pushes that never arrive.
if not _settings.apns_private_key:
    logging.getLogger(__name__).warning(
        "APNS_PRIVATE_KEY is unset — /internal/trigger-review will report "
        "sent=false reason=no_devices and no push will be delivered."
    )

app.include_router(authentication.router)
client_dependencies = [Depends(require_user)]
app.include_router(cards.router, dependencies=client_dependencies)
app.include_router(captures.router, dependencies=client_dependencies)
app.include_router(sessions.router, dependencies=client_dependencies)
app.include_router(devices.router, dependencies=client_dependencies)
app.include_router(settings.router, dependencies=client_dependencies)
app.include_router(study_plan.router, dependencies=client_dependencies)
app.include_router(materials.router, dependencies=client_dependencies)
app.include_router(internal.router)


@app.exception_handler(LLMError)
async def llm_unavailable(_request: Request, exc: LLMError) -> JSONResponse:
    """Scoring failed, so nothing was written.

    503 rather than 500 so the client can distinguish "retry this exact payload"
    from a genuine bug — the answer is still held client-side and the inline
    "Couldn't submit — your answer is saved" retry re-posts it unchanged.
    """
    logging.getLogger(__name__).error("llm unavailable: %s", exc)
    return JSONResponse(status_code=503, content={"detail": "scoring_unavailable"})


@app.get("/health")
async def health() -> dict[str, str]:
    async with session_factory() as session:
        await session.exec(text("SELECT 1"))
    return {"status": "ok"}
