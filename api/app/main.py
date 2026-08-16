import asyncio
import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress

from fastapi import Depends, FastAPI, Request
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.auth import AuthMiddleware, require_user
from app.config import get_settings
from app.consent_policy import LATEST_POLICY_VERSION, policy_for
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
from app.services.abuse import AbuseProtectionMiddleware
from app.services.llm import LLMError
from app.services.materials import run_import_sweeper
from app.services.review_poller import run_review_poller

_settings = get_settings()
logging.basicConfig(level=_settings.log_level)
EXPECTED_SCHEMA_REVISION = "0025"


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    poller: asyncio.Task[None] | None = None
    # One durable supervisor replaces one indefinitely waiting task per import.
    # It also recovers work that becomes stale after startup.
    import_sweeper = asyncio.create_task(
        run_import_sweeper(), name="material-import-sweeper"
    )
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
        import_sweeper.cancel()
        with suppress(asyncio.CancelledError):
            await import_sweeper
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
app.add_middleware(AbuseProtectionMiddleware)
# Added last so it is outermost in Starlette's stack: obviously missing or
# malformed credentials are rejected before the server reads even a bounded
# request body. Public auth routes still flow through abuse protection.
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
    # Provider errors can contain model output or user-derived prompt fragments.
    # Keep production logs useful without copying that material into a second
    # retention surface.
    logging.getLogger(__name__).error("llm unavailable type=%s", type(exc).__name__)
    return JSONResponse(status_code=503, content={"detail": "scoring_unavailable"})


@app.get("/health")
async def health() -> dict[str, str | int | bool]:
    async with session_factory() as session:
        await session.exec(text("SELECT 1"))
    required_policy = policy_for(get_settings().ai_consent_required_policy_version)
    return {
        "status": "ok",
        "ai_consent_required_policy_version": required_policy.version,
        "ai_consent_latest_supported_policy_version": LATEST_POLICY_VERSION,
        "ai_consent_minimum_ios_build": required_policy.minimum_ios_build,
        "ai_consent_enforcement_enabled": get_settings().ai_consent_enforcement_enabled,
    }


@app.get("/live")
async def live() -> dict[str, str]:
    """Process liveness only; never waits for a downstream service."""
    return {"status": "alive"}


@app.get("/ready")
async def ready() -> JSONResponse:
    """Deployment readiness: database connectivity and exact migration head."""
    try:
        async with session_factory() as session:
            rows = (await session.exec(text("SELECT version_num FROM alembic_version"))).all()
    except SQLAlchemyError:
        logging.getLogger(__name__).error("readiness database check failed")
        return JSONResponse(
            status_code=503,
            content={"status": "not_ready", "reason": "database_unavailable"},
        )

    revisions = {str(row[0]) for row in rows}
    if revisions != {EXPECTED_SCHEMA_REVISION}:
        logging.getLogger(__name__).error(
            "readiness schema mismatch expected=%s actual=%s",
            EXPECTED_SCHEMA_REVISION,
            sorted(revisions),
        )
        return JSONResponse(
            status_code=503,
            content={
                "status": "not_ready",
                "reason": "schema_mismatch",
                "expected_schema_revision": EXPECTED_SCHEMA_REVISION,
            },
        )
    return JSONResponse(
        content={
            "status": "ready",
            "schema_revision": EXPECTED_SCHEMA_REVISION,
            "ai_consent_enforcement_enabled": (
                get_settings().ai_consent_enforcement_enabled
            ),
        }
    )
