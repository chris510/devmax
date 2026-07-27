"""Seed scheduling.

`--weeks-through` cannot prevent a day-one flood on its own — it controls which
cards load, not when they come due. The property that matters is that no more
cards come due per day than the push budget can deliver, so that is what is
asserted here rather than the arithmetic that produces it.
"""

import collections
import json
from datetime import date, timedelta
from pathlib import Path

import pytest

from app.db import is_local_database
from app.models import DELIVERY_CONVERSATIONAL, DELIVERY_DESK
from app.seed import _schedule, delivery_mode_for

START = date(2026, 8, 3)
CARDS_JSON = Path(__file__).resolve().parent.parent / "cards.json"


def study_plan() -> list[dict]:
    return json.loads(CARDS_JSON.read_text())


def week_one(n: int, category: str = "Core Concept") -> list[dict]:
    return [{"topic": f"t{i}", "category": category, "target_week": 1} for i in range(n)]


def due_by_mode(entries: list[dict], per_day: int, mode: str) -> collections.Counter:
    due = _schedule(entries, START, per_day)
    return collections.Counter(
        day
        for entry, day in zip(entries, due, strict=True)
        if delivery_mode_for(entry.get("category", "Unsorted")) == mode
    )


def test_the_real_study_plan_never_exceeds_the_daily_push_budget() -> None:
    """The whole point: seeding the whole plan must not flood day one.

    The curriculum additions (docs/CURRICULUM.md) put 16/23/19 conversational cards
    in weeks 1-3, past the 14 that fit at two a day. `_schedule` clamps the overflow
    onto each week's last day rather than spilling it into the next, so at two a day
    week 2 piles 11 cards onto its seventh. The plan needs four a day now; day one is
    one session's worth at either rate.
    """
    assert due_by_mode(study_plan(), 2, DELIVERY_CONVERSATIONAL)[START] == 2, (
        "day one must show one session's worth, not the cohort"
    )
    assert max(due_by_mode(study_plan(), 4, DELIVERY_CONVERSATIONAL).values()) <= 4


def test_the_budget_follows_reviews_per_day() -> None:
    entries = week_one(6)

    assert max(due_by_mode(entries, 1, DELIVERY_CONVERSATIONAL).values()) == 1
    assert max(due_by_mode(entries, 3, DELIVERY_CONVERSATIONAL).values()) == 3


def test_desk_cards_are_dealt_one_a_day_whatever_the_push_budget_is() -> None:
    """Desk cards never enter the push loop, so they don't share its budget."""
    counts = due_by_mode(week_one(5, "Coding Pattern"), 2, DELIVERY_DESK)

    assert max(counts.values()) == 1
    assert sorted(counts) == [START + timedelta(days=i) for i in range(5)]


def test_each_week_starts_seven_days_after_the_last() -> None:
    entries = [
        {"topic": "a", "category": "Core Concept", "target_week": 1},
        {"topic": "b", "category": "Core Concept", "target_week": 2},
        {"topic": "c", "category": "Core Concept", "target_week": 3},
    ]
    due = _schedule(entries, START, per_day=2)

    assert due[0] == START
    assert due[1] == START + timedelta(days=7)
    assert due[2] == START + timedelta(days=14)


def test_an_oversized_week_never_spills_into_the_next_one() -> None:
    """Clamped to the last day of its own week, so week N+1 still starts on time."""
    due = _schedule(week_one(40), START, per_day=2)

    assert max(due) == START + timedelta(days=6)


def test_a_card_with_no_target_week_is_due_immediately() -> None:
    """Matches POST /cards with {"schedule": "now"}."""
    due = _schedule([{"topic": "ad hoc", "category": "Core Concept"}], START, per_day=2)

    assert due[0] == START


def test_categories_map_to_the_delivery_modes_the_spec_lists() -> None:
    for category in ("Coding Warmup", "Coding Pattern", "Tier 2 Practical Build"):
        assert delivery_mode_for(category) == DELIVERY_DESK
    for category in (
        "Core Concept",
        "Key Technology",
        "System Design Pattern",
        "System Design Problem",
        "Company-Specific Problem",
        "Behavioral",
        "Unsorted",
    ):
        assert delivery_mode_for(category) == DELIVERY_CONVERSATIONAL


def test_the_shipped_study_plan_matches_its_documented_shape() -> None:
    entries = study_plan()
    modes = collections.Counter(delivery_mode_for(e["category"]) for e in entries)

    assert len(entries) == 126
    assert modes[DELIVERY_CONVERSATIONAL] == 99
    assert modes[DELIVERY_DESK] == 27
    # seed.py raises KeyError without a topic, and dedupes on it.
    assert all(e.get("topic") and e.get("target_week") for e in entries)
    assert len({e["topic"] for e in entries}) == len(entries)


@pytest.mark.parametrize(
    "url",
    [
        # A private-network address is still a production database. This is the case
        # that would be lost by reusing the TLS trusted-network predicate here, which
        # deliberately does treat railway.internal as trusted.
        "postgresql+asyncpg://postgres:p@postgres.railway.internal:5432/railway",
        "postgresql+asyncpg://postgres:p@metro.proxy.rlwy.net:41234/railway",
        # An allowlist has to hold for hosts nobody thought of, not just one vendor.
        "postgresql+asyncpg://u:p@db.example-cloud.com/wc",
        "postgresql+asyncpg://u:p@ep-x.aws.neon.tech/wc",
        "postgresql+asyncpg://u:p@10.0.0.7/wc",
    ],
)
def test_the_fixtures_guard_treats_any_unrecognised_host_as_real(url: str) -> None:
    assert not is_local_database(url)


@pytest.mark.parametrize(
    "url",
    [
        "postgresql+asyncpg://postgres@127.0.0.1/devmax",
        "postgresql+asyncpg://postgres@localhost:55432/devmax",
        "sqlite+aiosqlite:///:memory:",
    ],
)
def test_local_databases_still_accept_fixtures(url: str) -> None:
    assert is_local_database(url)
