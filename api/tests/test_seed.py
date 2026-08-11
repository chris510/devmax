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
from sqlmodel import select

from app.db import is_local_database
from app.models import (
    DELIVERY_CONVERSATIONAL,
    DELIVERY_DESK,
    FOUNDER_USER_ID,
    Card,
    PendingCapture,
    Session,
    User,
)
from app.seed import (
    SeedGroundingError,
    _schedule,
    _schedule_entries,
    _selected_entries,
    _validated_grounding,
    delivery_mode_for,
    load_fixtures,
    load_from_file,
    retire_from_file,
)
from tests.conftest import make_card

START = date(2026, 8, 3)
API = Path(__file__).resolve().parent.parent
CARDS_JSON = API / "cards.json"
LEGACY_JSON = API / "archive" / "cards-legacy-126.json"


def study_plan() -> list[dict]:
    return json.loads(CARDS_JSON.read_text())


def week_one(n: int, category: str = "Core Concept") -> list[dict]:
    return [{"topic": f"t{i}", "category": category, "target_week": 1} for i in range(n)]


def approved_entry(**changes) -> dict:
    entry = {
        "topic": "Cursor pagination",
        "category": "Core Concept",
        "target_week": 1,
        "source_url": "https://example.com/cursor",
        "source_section": "Cursor pagination",
        "source_label": "Reviewed source",
        "answer_basis": "Resume strictly after the last stable ordered key.",
        "answer_rubric": {
            "mechanism": "Use the last stable ordered key.",
            "acceptable_alternative": "Keyset pagination.",
            "trade_off": "No arbitrary page jumps.",
            "failure_mode": "A mutable key duplicates or skips rows.",
            "misconception": "A cursor is not a page number.",
        },
        "canonical_question": "How does a cursor preserve stable traversal?",
        "grounding_status": "approved",
    }
    entry.update(changes)
    return entry


def due_by_mode(entries: list[dict], per_day: int, mode: str) -> collections.Counter:
    due = _schedule(entries, START, per_day)
    return collections.Counter(
        day
        for entry, day in zip(entries, due, strict=True)
        if delivery_mode_for(entry.get("category", "Unsorted")) == mode
    )


def test_the_real_study_plan_never_exceeds_the_daily_push_budget() -> None:
    """The whole point: seeding the whole plan must not flood day one.

    The 12-week curriculum has six new cards in each of its nine teaching weeks,
    below the fourteen that fit at the shipped `reviews_per_day` of two. Weeks
    10-12 deliberately contain no generic cards: they are for mocks, company
    overlays, and prompts earned from observed gaps.
    """
    counts = due_by_mode(study_plan(), 2, DELIVERY_CONVERSATIONAL)

    assert counts[START] == 2, "day one must show one session's worth, not the cohort"
    assert max(counts.values()) <= 2


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


def test_activate_week_selects_only_that_week_and_rebases_its_schedule() -> None:
    entries = [
        {"topic": "a", "category": "Core Concept", "target_week": 1},
        {"topic": "b", "category": "Core Concept", "target_week": 4},
        {"topic": "c", "category": "Core Concept", "target_week": 4},
    ]

    selected = _selected_entries(entries, weeks_through=2, activate_week=4)
    schedule_entries = _schedule_entries(selected, activate_week=4)

    assert [entry["topic"] for entry in selected] == ["b", "c"]
    assert all(entry["target_week"] == 4 for entry in selected)
    assert _schedule(schedule_entries, START, per_day=2) == [START, START]


def test_bulk_selection_keeps_the_original_weeks_through_behavior() -> None:
    entries = [
        {"topic": "a", "target_week": 1},
        {"topic": "b", "target_week": 2},
        {"topic": "c", "target_week": 3},
    ]

    assert [entry["topic"] for entry in _selected_entries(entries, 2, None)] == ["a", "b"]


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
    weeks = collections.Counter(e["target_week"] for e in entries)

    assert len(entries) == 54
    assert modes[DELIVERY_CONVERSATIONAL] == 54
    assert modes[DELIVERY_DESK] == 0
    assert weeks == {week: 6 for week in range(1, 10)}
    # seed.py raises KeyError without a topic, and dedupes on it.
    assert all(e.get("topic") and e.get("target_week") for e in entries)
    assert len({e["topic"] for e in entries}) == len(entries)
    practitioner_sources = [
        entry
        for entry in entries
        if entry["source_url"].startswith("https://www.infoq.com/")
    ]
    assert len(practitioner_sources) == 1
    assert practitioner_sources[0]["evidence"] == "primary_practitioner_talk"
    assert all(
        entry["source_url"].startswith("https://www.hellointerview.com/")
        or entry in practitioner_sources
        for entry in entries
    )
    assert all(e.get("activation_prerequisite") for e in entries)
    assert all(
        e["category"] == "System Design Pattern" for e in entries if 4 <= e["target_week"] <= 6
    )
    assert all(e["target_week"] >= 7 for e in entries if e["category"] == "Key Technology")
    assert not any(
        e["category"]
        in {
            "Behavioral",
            "Coding Pattern",
            "Coding Warmup",
            "Company-Specific Problem",
            "System Design Problem",
            "Tier 2 Practical Build",
        }
        for e in entries
    )


def test_the_week_one_grounding_pack_is_approved_and_complete() -> None:
    entries = study_plan()
    approved = [entry for entry in entries if entry.get("grounding_status") == "approved"]
    drafts = [entry for entry in entries if entry.get("grounding_status") == "draft_review"]
    remaining = [entry for entry in entries if "grounding_status" not in entry]

    assert len(approved) == 6
    assert {entry["target_week"] for entry in approved} == {1}
    assert all(_validated_grounding(entry) is not None for entry in approved)
    assert len(drafts) == 1
    assert drafts[0]["target_week"] == 4
    assert drafts[0]["topic"].startswith("Fan-out mechanics")
    assert all(
        drafts[0].get(field)
        for field in (
            "canonical_question",
            "source_url",
            "source_section",
            "source_label",
            "answer_basis",
            "answer_rubric",
        )
    )
    assert len(remaining) == 47


def test_an_approved_conversational_seed_still_requires_every_grounding_field() -> None:
    entry = approved_entry(answer_rubric={"mechanism": "Only one field."})

    with pytest.raises(SeedGroundingError, match="answer_rubric.acceptable_alternative"):
        _validated_grounding(entry)


def test_desk_reference_material_does_not_need_a_scoring_rubric() -> None:
    assert _validated_grounding(
        {"topic": "Implement a heap", "category": "Coding Pattern"}
    ) is None


async def test_approved_seed_persists_the_reviewed_answer_authority(tmp_path, db) -> None:
    path = tmp_path / "approved.json"
    path.write_text(json.dumps([approved_entry()]))
    other = User()
    db.add(other)
    await db.flush()
    db.add(make_card(user_id=other.id, topic="Cursor pagination"))
    await db.commit()

    assert await load_from_file(path, weeks_through=1, start=START, db=db) == 1

    card = (
        await db.exec(
            select(Card).where(
                Card.user_id == FOUNDER_USER_ID,
                Card.topic == "Cursor pagination",
            )
        )
    ).one()
    assert card.source_label == "Reviewed source"
    assert card.answer_basis.startswith("Resume strictly")
    assert card.answer_rubric["failure_mode"].startswith("A mutable key")
    assert card.canonical_question == "How does a cursor preserve stable traversal?"
    assert len((await db.exec(select(Card).where(Card.user_id == other.id))).all()) == 1


async def test_one_draft_entry_prevents_the_entire_selected_cohort(tmp_path, db) -> None:
    path = tmp_path / "mixed.json"
    path.write_text(
        json.dumps(
            [
                approved_entry(topic="Approved first"),
                approved_entry(topic="Draft second", grounding_status="draft_review"),
            ]
        )
    )

    with pytest.raises(SeedGroundingError, match="Draft second"):
        await load_from_file(path, weeks_through=1, start=START, db=db)

    assert (await db.exec(select(Card))).all() == []


async def test_fixtures_ignore_a_public_users_same_topic(db) -> None:
    other = User()
    db.add(other)
    await db.flush()
    db.add(make_card(user_id=other.id, topic="Consistent hashing"))
    await db.commit()

    assert await load_fixtures(db=db) == 4

    founder_cards = (
        await db.exec(select(Card).where(Card.user_id == FOUNDER_USER_ID))
    ).all()
    assert {card.topic for card in founder_cards} == {
        "Consistent hashing",
        "Raft leader election",
        "Postgres index types",
        "Multi-source BFS",
    }
    assert len((await db.exec(select(Card).where(Card.user_id == other.id))).all()) == 1


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


# --- retiring a curriculum ---------------------------------------------------


def legacy_manifest() -> list[dict]:
    return json.loads(LEGACY_JSON.read_text())


def test_the_retire_manifest_shares_no_topic_with_any_live_deck() -> None:
    """The standing guard that makes `--retire-file` safe to run.

    Retirement matches on topic, so a curriculum edit that reintroduced a legacy
    topic would turn the prune into silent data loss on a live card. Nothing
    stops that but this assertion.
    """
    live = {entry["topic"] for entry in study_plan()}
    for path in sorted((API / "library").glob("*.json")) + sorted((API / "modules").glob("*.json")):
        live |= {entry["topic"] for entry in json.loads(path.read_text())}

    retiring = {entry["topic"] for entry in legacy_manifest()}
    assert retiring & live == set()


def test_the_retire_manifest_has_the_shape_the_prune_expects() -> None:
    entries = legacy_manifest()
    topics = [entry["topic"] for entry in entries]

    assert len(entries) == 126
    assert len(set(topics)) == len(topics)


@pytest.fixture
def manifest(tmp_path):
    """A one-topic retire manifest. The topic is `Doomed`; `Kept` never appears."""
    path = tmp_path / "retire.json"
    path.write_text(json.dumps([{"topic": "Doomed"}]))
    return path


async def test_retire_deletes_only_the_topics_in_the_manifest(db, manifest) -> None:
    db.add(make_card(topic="Doomed"))
    db.add(make_card(topic="Kept"))
    await db.commit()

    assert await retire_from_file(manifest, db=db) == (["Doomed"], 0)
    assert [c.topic for c in (await db.exec(select(Card))).all()] == ["Kept"]


async def test_retire_removes_the_sessions_that_belonged_to_the_card(db, manifest) -> None:
    """Asserted on SQLite too, which is why the delete is explicit.

    `sessions.card_id` is ON DELETE CASCADE, but SQLite does not enforce foreign
    keys unless asked and nothing here asks. Leaning on the cascade would let this
    test pass over orphan rows that production would never produce.
    """
    doomed = make_card(topic="Doomed")
    kept = make_card(topic="Kept")
    db.add(doomed)
    db.add(kept)
    await db.commit()
    db.add(Session(card_id=doomed.id, question_asked="q1"))
    db.add(Session(card_id=doomed.id, question_asked="q2"))
    db.add(Session(card_id=kept.id, question_asked="q3"))
    await db.commit()

    assert await retire_from_file(manifest, db=db) == (["Doomed"], 2)
    survivors = (await db.exec(select(Session))).all()
    assert [s.question_asked for s in survivors] == ["q3"]


async def test_retire_removes_the_activated_capture_that_points_at_the_card(
    db, manifest
) -> None:
    card = make_card(topic="Doomed")
    db.add(card)
    await db.flush()
    db.add(
        PendingCapture(
            topic=card.topic,
            status="activated",
            activated_card_id=card.id,
        )
    )
    await db.commit()

    await retire_from_file(manifest, db=db)

    assert (await db.exec(select(PendingCapture))).all() == []


async def test_a_dry_run_reports_without_deleting(db, manifest) -> None:
    card = make_card(topic="Doomed")
    db.add(card)
    await db.commit()
    db.add(Session(card_id=card.id, question_asked="q1"))
    await db.commit()

    assert await retire_from_file(manifest, dry_run=True, db=db) == (["Doomed"], 1)
    assert len((await db.exec(select(Card))).all()) == 1
    assert len((await db.exec(select(Session))).all()) == 1


async def test_retiring_twice_is_a_no_op(db, manifest) -> None:
    db.add(make_card(topic="Doomed"))
    await db.commit()

    await retire_from_file(manifest, db=db)
    assert await retire_from_file(manifest, db=db) == ([], 0)


async def test_a_manifest_topic_absent_from_the_database_is_not_an_error(db) -> None:
    """The real case: the legacy deck against a database that never held it."""
    assert await retire_from_file(LEGACY_JSON, db=db) == ([], 0)


async def test_retirement_never_crosses_the_founder_ownership_boundary(db, manifest) -> None:
    other = User()
    db.add(other)
    await db.flush()
    founder_card = make_card(topic="Doomed")
    other_card = make_card(user_id=other.id, topic="Doomed")
    db.add(founder_card)
    db.add(other_card)
    await db.flush()
    db.add(Session(card_id=founder_card.id, question_asked="founder session"))
    db.add(Session(card_id=other_card.id, question_asked="other session"))
    db.add(
        PendingCapture(
            user_id=FOUNDER_USER_ID,
            topic="Doomed",
            status="activated",
            activated_card_id=founder_card.id,
        )
    )
    db.add(
        PendingCapture(
            user_id=other.id,
            topic="Doomed",
            status="activated",
            activated_card_id=other_card.id,
        )
    )
    await db.commit()

    assert await retire_from_file(manifest, db=db) == (["Doomed"], 1)

    assert (await db.exec(select(Card))).all() == [other_card]
    assert [row.question_asked for row in (await db.exec(select(Session))).all()] == [
        "other session"
    ]
    assert (await db.exec(select(PendingCapture))).all()[0].user_id == other.id
