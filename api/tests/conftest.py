import os
import uuid
from collections.abc import AsyncIterator
from datetime import date

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
os.environ.setdefault("API_KEY", "test-api-key")
os.environ.setdefault("CRON_SECRET", "test-cron-secret")

import pytest  # noqa: E402
from httpx import ASGITransport, AsyncClient  # noqa: E402
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402
from sqlmodel import SQLModel  # noqa: E402
from sqlmodel.ext.asyncio.session import AsyncSession  # noqa: E402

from app.db import get_session  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Card, Settings  # noqa: E402

API_HEADERS = {"X-API-Key": "test-api-key"}
CRON_HEADERS = {"X-Cron-Secret": "test-cron-secret"}


@pytest.fixture
async def db() -> AsyncIterator[AsyncSession]:
    """A fresh in-memory schema per test.

    StaticPool keeps every connection pointed at the same in-memory database —
    without it each connection gets its own empty one.
    """
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
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
        next_review_at=date(2026, 7, 24),
        mastery_summary="",
    )
    return Card(**{**defaults, **overrides})
