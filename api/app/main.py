import logging

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.auth import AuthMiddleware
from app.config import get_settings
from app.db import session_factory
from app.routers import cards, devices, internal, sessions, settings, study_plan
from app.services.llm import LLMError

_settings = get_settings()
logging.basicConfig(level=_settings.log_level)

app = FastAPI(title="Devmax API", docs_url=None, redoc_url=None, openapi_url=None)
app.add_middleware(AuthMiddleware)

# Not fatal: deploying before the Apple credentials exist is the recommended order.
# But send_push() returns 0 silently in that state, so without this the only symptom
# is pushes that never arrive.
if not _settings.apns_private_key:
    logging.getLogger(__name__).warning(
        "APNS_PRIVATE_KEY is unset — /internal/trigger-review will report "
        "sent=false reason=no_devices and no push will be delivered."
    )

app.include_router(cards.router)
app.include_router(sessions.router)
app.include_router(devices.router)
app.include_router(settings.router)
app.include_router(study_plan.router)
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
        await session.execute(text("SELECT 1"))
    return {"status": "ok"}
