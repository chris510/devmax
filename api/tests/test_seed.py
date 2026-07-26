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

from app.models import DELIVERY_CONVERSATIONAL, DELIVERY_DESK
from app.seed import _looks_like_production, _schedule, delivery_mode_for

START = date(2026, 8, 3)
CARDS_JSON = Path(__file__).resolve().parent.parent / "cards.json"


def study_plan() -> list[dict]:
    return json.loads(CARDS_JSON.read_text())


def due_by_mode(entries: list[dict], per_day: int, mode: str) -> collections.Counter:
    due = _schedule(entries, START, per_day)
    return collections.Counter(
        day
        for i, day in due.items()
        if delivery_mode_for(entries[i].get("category", "Unsorted")) == mode
    )


def test_the_real_study_plan_never_exceeds_the_daily_push_budget() -> None:
    """The whole point: seeding all 111 cards must not flood day one."""
    counts = due_by_mode(study_plan(), 2, DELIVERY_CONVERSATIONAL)

    assert counts[START] == 2, "day one must show one session's worth, not the cohort"
    assert max(counts.values()) <= 2


def test_the_budget_follows_reviews_per_day() -> None:
    entries = [{"topic": f"t{i}", "category": "Core Concept", "target_week": 1} for i in range(6)]

    assert max(due_by_mode(entries, 1, DELIVERY_CONVERSATIONAL).values()) == 1
    assert max(due_by_mode(entries, 3, DELIVERY_CONVERSATIONAL).values()) == 3


def test_desk_cards_are_dealt_one_a_day_whatever_the_push_budget_is() -> None:
    """Desk cards never enter the push loop, so they don't share its budget."""
    entries = [{"topic": f"t{i}", "category": "Coding Pattern", "target_week": 1} for i in range(5)]

    counts = due_by_mode(entries, 2, DELIVERY_DESK)

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
    entries = [{"topic": f"t{i}", "category": "Core Concept", "target_week": 1} for i in range(40)]

    due = _schedule(entries, START, per_day=2)

    assert max(due.values()) == START + timedelta(days=6)


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

    assert len(entries) == 111
    assert modes[DELIVERY_CONVERSATIONAL] == 84
    assert modes[DELIVERY_DESK] == 27
    # seed.py raises KeyError without a topic, and dedupes on it.
    assert all(e.get("topic") and e.get("target_week") for e in entries)
    assert len({e["topic"] for e in entries}) == len(entries)


def test_the_fixtures_guard_recognises_a_neon_url() -> None:
    assert _looks_like_production("postgresql+asyncpg://u:p@ep-x.aws.neon.tech/wc")
    assert not _looks_like_production("postgresql+asyncpg://postgres@127.0.0.1/warmcache")
