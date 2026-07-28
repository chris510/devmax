import os
import uuid
from collections.abc import AsyncIterator
from datetime import date

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
os.environ.setdefault("API_KEY", "test-api-key")
os.environ.setdefault("CRON_SECRET", "test-cron-secret")

import pytest  # noqa: E402
from httpx import ASGITransport, AsyncClient  # noqa: E402
from sqlalchemy import text  # noqa: E402
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402
from sqlmodel import SQLModel  # noqa: E402
from sqlmodel.ext.asyncio.session import AsyncSession  # noqa: E402

from app.db import get_session  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Card, Settings  # noqa: E402
from app.routers.deps import now_in  # noqa: E402

API_HEADERS = {"X-API-Key": "test-api-key"}
CRON_HEADERS = {"X-Cron-Secret": "test-cron-secret"}


def local_today() -> date:
    """The calendar day the app compares due dates against.

    Every due comparison goes through `routers/deps.local_today`, which reads the
    settings timezone — not the runner's. Using `date.today()` in a test makes it
    pass or fail depending on the time of day: on a UTC machine the two disagree
    from 00:00 to 07:00 UTC, which is most of a Pacific afternoon.
    """
    return now_in(Settings().timezone).date()

# The suite runs on in-memory SQLite by default — fast, hermetic, no services.
# Point TEST_DATABASE_URL at an already-migrated Postgres database to run the
# same suite against the real production schema instead, which is the only way
# to exercise JSONB, native UUID, timestamptz, and the four CHECK constraints
# that live in the migration but not in SQLModel.metadata:
#
#   createdb devmax_test
#   DATABASE_URL=postgresql+asyncpg://... uv run alembic upgrade head
#   TEST_DATABASE_URL=postgresql+asyncpg://... uv run pytest
TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL", "sqlite+aiosqlite:///:memory:")
TEST_ON_POSTGRES = TEST_DATABASE_URL.startswith("postgresql")


@pytest.fixture
async def db() -> AsyncIterator[AsyncSession]:
    """A fresh schema per test.

    On SQLite, StaticPool keeps every connection pointed at the same in-memory
    database — without it each connection gets its own empty one. On Postgres
    the schema is created by `alembic upgrade head` out of band, so each test
    truncates instead of recreating.
    """
    if TEST_ON_POSTGRES:
        engine = create_async_engine(TEST_DATABASE_URL, poolclass=StaticPool)
        async with engine.begin() as conn:
            await conn.execute(
                text("TRUNCATE cards, sessions, device_tokens, settings RESTART IDENTITY CASCADE")
            )
    else:
        engine = create_async_engine(
            TEST_DATABASE_URL,
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        async with engine.begin() as conn:
            await conn.run_sync(SQLModel.metadata.create_all)

    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        session.add(Settings(id=1))
        await session.commit()
        yield session

    await engine.dispose()


@pytest.fixture
async def client(db: AsyncSession) -> AsyncIterator[AsyncClient]:
    app.dependency_overrides[get_session] = lambda: db
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.clear()


def make_card(**overrides) -> Card:
    defaults = dict(
        id=uuid.uuid4(),
        topic="Consistent hashing",
        category="Core Concept",
        delivery_mode="conversational",
        ease_factor=2.5,
        interval_days=1,
        repetitions=0,
        # Relative, not a fixed calendar day. With interval_days=1 the lapse
        # cutoff is two days, so a hardcoded past date silently reclassifies any
        # "solid" fixture as cold once real time drifts past it.
        next_review_at=local_today(),
        mastery_summary="",
    )
    return Card(**{**defaults, **overrides})


@pytest.fixture
def in_window(monkeypatch):
    """Pins the clock to 07:30 local — inside the default morning window.

    Every /internal/trigger-review test needs this; `_in_window` reads the wall
    clock, so without it the suite passes or fails depending on the hour it runs.
    """
    from datetime import datetime
    from zoneinfo import ZoneInfo

    from app.routers import internal

    # Only the time of day is pinned. Fixing the calendar day too made the
    # trigger-review tests depend on how far real time had drifted from it:
    # they build due dates with `local_today() - 3 days`, so once the real date
    # passed 2026-07-27 those cards landed in the pinned day's future and the
    # endpoint correctly answered `nothing_due`.
    monkeypatch.setattr(
        internal,
        "now_in",
        lambda tz: datetime.now(ZoneInfo(tz)).replace(
            hour=7, minute=30, second=0, microsecond=0
        ),
    )
