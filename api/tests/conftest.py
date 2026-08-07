import os
import uuid
from collections.abc import AsyncIterator
from datetime import date, datetime

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
from app.routers import study_plan as study_plan_router  # noqa: E402
from app.routers.deps import now_in  # noqa: E402
from app.services import llm  # noqa: E402

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
            # Every table, or a leftover row from the previous test leaks into the
            # next one. New tables must be added here as well as to models.py.
            await conn.execute(
                text(
                    "TRUNCATE cards, sessions, device_tokens, settings, "
                    "study_plans, study_plan_phases, study_plan_weeks, "
                    "study_plan_items, study_plan_item_dependencies, "
                    "study_plan_revisions, study_plan_guide_drafts, "
                    "study_plan_practice_debriefs, "
                    "study_plan_card_proposals, "
                    "study_plan_card_proposal_acceptances, "
                    "study_plan_card_links, study_plan_duplications "
                    "RESTART IDENTITY CASCADE"
                )
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


def local_today_at(hour: int, minute: int = 0, tz: str | None = None) -> datetime:
    """Today at a local wall-clock time, in the configured zone.

    Only the time of day is set; the calendar day stays today. Fixing the date
    too made the trigger-review tests depend on how far real time had drifted
    from it: they build due dates with `local_today() - 3 days`, so once the
    real date passed the pinned one those cards landed in the pinned day's
    future and the endpoint correctly answered `nothing_due`.
    """
    return now_in(tz or Settings().timezone).replace(
        hour=hour, minute=minute, second=0, microsecond=0
    )


def pin_clock(monkeypatch, hour: int, minute: int = 0) -> None:
    """Pin the local time `/internal/trigger-review` sees.

    `_active_window` reads the wall clock, so without this the suite passes or
    fails depending on the hour it runs. Patches `internal.now_in`, not
    `deps.now_in` — the router imports the name into its own namespace.
    """
    from app.routers import internal

    monkeypatch.setattr(internal, "now_in", lambda tz: local_today_at(hour, minute, tz))


@pytest.fixture
def in_window(monkeypatch):
    """07:30 local — inside the default 07:10–08:30 morning window."""
    pin_clock(monkeypatch, 7, 30)


# --- Study Plan fixtures ----------------------------------------------------
#
# Shared by tests/test_study_plan_api.py and tests/test_study_plan_invariants.py.
# They live here rather than being imported between test modules, which would
# shadow the fixture name at every use site.

GUIDE = (
    "Week 1: The request path. Trace a request from DNS through the load balancer "
    "to the database and back.\n"
    "Week 2: Storage engines and transactions. B-trees, LSM trees, isolation levels.\n"
    "Week 3: Relational and NoSQL systems. Postgres, Redis, DynamoDB, Cassandra.\n"
    "Week 4: Caching, sharding, consistent hashing, and CAP.\n"
    "Prerequisite: finish consistent hashing before sharding strategies.\n"
) * 3


def _item(key, week, order, **overrides):
    defaults = {
        "key": key,
        "week_index": week,
        "guide_order": order,
        "type": "learn",
        "priority": "core",
        "full_title": f"Item {key}",
        "why_it_matters": "It connects the rest of the week together.",
        "done_when": "You can explain it closed-book in two minutes.",
        "estimate_minutes": 60,
        "estimate_source": "imported",
        "estimate_confidence": "high",
        "origin": "imported",
        "source_item_key": None,
        "source_start": 0,
        "source_end": 20,
        "source_excerpt": GUIDE[0:20],
        "parser_interpretation": "",
    }
    return {**defaults, **overrides}


def import_payload(**overrides):
    base = {
        "subject": "Senior backend interview",
        "subject_slug": "system-design",
        "supports_technical_recall_cards": True,
        "plan_title": "Senior backend interview",
        "phases": [
            {
                "index": 1,
                "full_title": "Foundations of the request path",
                "overview_title": "Foundations",
                "description": "Concepts every later design uses.",
            },
            {
                "index": 2,
                "full_title": "Concrete technologies",
                "overview_title": "Technologies",
                "description": "How systems implement those foundations.",
            },
        ],
        "weeks": [
            {"index": 1, "phase_index": 1, "full_title": "The request path",
             "overview_title": "Request path"},
            {"index": 2, "phase_index": 1, "full_title": "Storage engines",
             "overview_title": "Storage engines"},
            {"index": 3, "phase_index": 2, "full_title": "Relational and NoSQL",
             "overview_title": "Databases"},
            {"index": 4, "phase_index": 2, "full_title": "Caching and sharding",
             "overview_title": "Caching"},
        ],
        "items": [
            _item("L1", 1, 1),
            _item("L2", 2, 2),
            _item("L3", 3, 3),
            _item("L4", 4, 4),
            _item("P4", 4, 5, type="practice", priority="optional", estimate_minutes=30),
        ],
        "dependencies": [],
        "unresolved_estimates": [],
        "possible_omissions": [],
    }
    return {**base, **overrides}


@pytest.fixture
def stub_import(monkeypatch):
    """Replace the importer wholesale. `response` is mutable per test."""

    class Stub:
        response = import_payload()
        error: Exception | None = None
        calls = 0

    async def fake(**_kwargs):
        Stub.calls += 1
        if Stub.error is not None:
            raise Stub.error
        return Stub.response

    monkeypatch.setattr(llm, "import_guide", fake)
    monkeypatch.setattr(study_plan_router.llm, "import_guide", fake)
    Stub.response = import_payload()
    Stub.error = None
    Stub.calls = 0
    return Stub


@pytest.fixture
def stub_cards(monkeypatch):
    class Stub:
        candidates: list = []
        calls = 0
        last_kwargs: dict = {}

    async def fake(**kwargs):
        Stub.calls += 1
        Stub.last_kwargs = kwargs
        return Stub.candidates

    monkeypatch.setattr(llm, "propose_cards", fake)
    monkeypatch.setattr(study_plan_router.llm, "propose_cards", fake)
    Stub.candidates = []
    Stub.calls = 0
    Stub.last_kwargs = {}
    return Stub


def gate(all_passed=True, failing=3):
    return [
        {
            "question_index": n,
            "passed": all_passed or n != failing,
            "reason": "Because." if (all_passed or n != failing) else "Tests a definition.",
        }
        for n in range(1, 6)
    ]
