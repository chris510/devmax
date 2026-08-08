"""Load cards into the database.

Two sources:

* ``cards.json`` — the 54-card system-design recall spine
  (spec.md §Seeding, docs/CURRICULUM.md). Nine teaching weeks followed by three
  weeks of mocks and gap-driven additions. Activate one week only after its
  source lessons are complete.
* ``library/*.json`` — reference material that is never part of the automatic
  push curriculum. Coding lives here because implementation practice happens in
  an editor; seed a card only when external practice proves the recall prompt is
  useful.
* ``modules/*.json`` — company-shaped card sets in the same schema, seeded on
  demand when a loop is scheduled rather than as part of the daily rotation.
* ``--fixtures`` — the three cards from the design prototype, with their invented
  session history. Every screenshot in the design handoff depicts these, so this
  is what makes the designs reproducible against a real server during dev. Never
  load them into a real study queue; the guard in main() refuses by default.

    uv run python -m app.seed --fixtures
    uv run python -m app.seed --file cards.json --activate-week 1 --start-date 2026-08-03
    uv run python -m app.seed --retire-file archive/cards-legacy-126.json --dry-run

``--activate-week`` selects exactly one curriculum week and schedules it from the
given start date, regardless of its original week number. This makes lesson
completion—not the calendar—the gate. The older ``--weeks-through`` bulk path
remains useful for local verification and clean-room imports.

``--retire-file`` is the only path that deletes. Loading is additive and dedupes
by topic, so swapping a curriculum needs an explicit retirement of the old one —
naming the manifest to delete rather than diffing against the current deck, so
``library/``, ``modules/`` and gap-driven cards stay out of reach.
"""

import argparse
import asyncio
import json
from collections import defaultdict
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.config import get_settings
from app.db import is_local_database, session_factory
from app.models import (
    DELIVERY_CONVERSATIONAL,
    DELIVERY_DESK,
    STATUS_COMPLETE,
    Card,
    PendingCapture,
    Session,
)
from app.routers.deps import get_settings_row, local_today, now_in
from app.services.card_lifecycle import Grounding, GroundingError

# Coding problems need a keyboard and an hour, not a two-minute voice session.
DESK_CATEGORIES = {"Coding Warmup", "Coding Pattern", "Tier 2 Practical Build"}


def delivery_mode_for(category: str) -> str:
    return DELIVERY_DESK if category in DESK_CATEGORIES else DELIVERY_CONVERSATIONAL


DAYS_PER_WEEK = 7
GROUNDING_APPROVED = "approved"


class SeedGroundingError(ValueError):
    """A conversational manifest entry is not safe to activate."""


def _validated_grounding(entry: dict) -> Grounding | None:
    """Return approved grounding for a conversational card, or fail closed.

    Desk material never enters scoring, so its existing manifest format remains
    valid. A conversational seed is an activation path and must cross the same
    authority boundary as Capture and Study Plan proposal acceptance.
    """
    category = entry.get("category", "Unsorted")
    if delivery_mode_for(category) == DELIVERY_DESK:
        return None
    if entry.get("grounding_status") != GROUNDING_APPROVED:
        status = entry.get("grounding_status", "missing")
        raise SeedGroundingError(
            f"{entry.get('topic', '<untitled>')}: grounding status is {status!r}, "
            f"expected {GROUNDING_APPROVED!r}"
        )
    grounding = Grounding(
        source_url=entry.get("source_url", ""),
        source_section=entry.get("source_section", ""),
        source_label=entry.get("source_label", ""),
        answer_basis=entry.get("answer_basis", ""),
        answer_rubric=entry.get("answer_rubric"),
        canonical_question=entry.get("canonical_question", ""),
    )
    try:
        return grounding.require_complete()
    except GroundingError as exc:
        raise SeedGroundingError(
            f"{entry.get('topic', '<untitled>')}: incomplete approved grounding "
            f"({', '.join(exc.missing)})"
        ) from exc


@asynccontextmanager
async def _session(db: AsyncSession | None) -> AsyncIterator[AsyncSession]:
    """Use the caller's session if there is one, otherwise open our own.

    The loaders were written to open `session_factory()` themselves, which is why
    nothing in tests/test_seed.py could touch a database. Threading an optional
    session through leaves every CLI call site unchanged and lets tests pass the
    `db` fixture in.
    """
    if db is not None:
        yield db
    else:
        async with session_factory() as owned:
            yield owned


def _schedule(entries: list[dict], start: date, per_day: int) -> list[date]:
    """Spread each week's cards across that week, one small batch per day.

    ``--weeks-through`` alone cannot prevent a day-one flood: it controls *which*
    cards load, not when they come due. Seeding every card with
    ``next_review_at = today`` puts the entire cohort in the queue at once, which is
    the opposite of what spec.md §Seeding is asking for.

    Cards are grouped by (target_week, delivery_mode) and dealt out ``per_day`` at a
    time — matching the push budget for conversational cards, one a day for desk
    cards, which never enter the push loop at all. The day index is clamped so a
    large week spills onto its own last day rather than into the following week.
    Returns one due date per entry, in the order given.
    """
    groups: dict[tuple[int | None, str], list[int]] = defaultdict(list)
    for i, entry in enumerate(entries):
        mode = delivery_mode_for(entry.get("category", "Unsorted"))
        groups[(entry.get("target_week"), mode)].append(i)

    due: list[date] = [start] * len(entries)
    for (week, mode), indices in groups.items():
        # A card with no target_week behaves like POST /cards {"schedule": "now"}.
        week_start = start if week is None else start + timedelta(days=DAYS_PER_WEEK * (week - 1))
        rate = max(per_day, 1) if mode == DELIVERY_CONVERSATIONAL else 1
        for position, index in enumerate(indices):
            offset = min(position // rate, DAYS_PER_WEEK - 1)
            due[index] = week_start + timedelta(days=offset)
    return due


def _within(entry: dict, weeks_through: int) -> bool:
    week = entry.get("target_week")
    return week is None or week <= weeks_through


def _selected_entries(
    entries: list[dict], weeks_through: int, activate_week: int | None
) -> list[dict]:
    if activate_week is not None:
        return [entry for entry in entries if entry.get("target_week") == activate_week]
    return [entry for entry in entries if _within(entry, weeks_through)]


def _schedule_entries(entries: list[dict], activate_week: int | None) -> list[dict]:
    if activate_week is None:
        return entries
    return [{**entry, "target_week": 1} for entry in entries]


async def load_from_file(
    path: Path,
    weeks_through: int,
    start: date | None = None,
    activate_week: int | None = None,
    db: AsyncSession | None = None,
) -> int:
    entries = _selected_entries(json.loads(path.read_text()), weeks_through, activate_week)
    grounding = [_validated_grounding(entry) for entry in entries]
    added = 0
    async with _session(db) as db:
        settings = await get_settings_row(db)
        begin = start or now_in(settings.timezone).date()

        # An activated week starts now. Keep the original target_week on Card so
        # Coverage can explain curriculum order, but schedule it as a fresh week 1.
        schedule_entries = _schedule_entries(entries, activate_week)
        due_dates = _schedule(schedule_entries, begin, settings.reviews_per_day)
        for entry, due, authority in zip(entries, due_dates, grounding, strict=True):
            existing = (await db.exec(select(Card).where(Card.topic == entry["topic"]))).first()
            if existing is not None:
                continue
            category = entry.get("category", "Unsorted")
            db.add(
                Card(
                    topic=entry["topic"],
                    category=category,
                    pattern=entry.get("pattern"),
                    source_company=entry.get("source_company"),
                    target_week=entry.get("target_week"),
                    delivery_mode=delivery_mode_for(category),
                    canonical_question=(authority.canonical_question if authority else None),
                    source_url=authority.source_url if authority else "",
                    source_section=authority.source_section if authority else "",
                    source_label=authority.source_label if authority else "",
                    answer_basis=authority.answer_basis if authority else "",
                    answer_rubric=(authority.answer_rubric or {}) if authority else {},
                    next_review_at=due,
                )
            )
            added += 1
        await db.commit()
    return added


async def retire_from_file(
    path: Path,
    dry_run: bool = False,
    db: AsyncSession | None = None,
) -> tuple[list[str], int]:
    """Delete every card whose topic appears in `path`, along with its sessions.

    Retirement is by **explicit manifest**, never "delete anything missing from
    cards.json". The same database also holds `library/` reference cards,
    `modules/` company overlays, and gap-driven cards added through POST /cards.
    None of those appear in the base manifest, and a curriculum swap has no
    business touching them — so the caller names exactly what is being retired.

    Sessions are deleted explicitly even though `sessions.card_id` is already
    ON DELETE CASCADE. The default test database is SQLite, which does not enforce
    foreign keys unless asked, so leaning on the cascade would mean the behaviour
    under test and the behaviour in production are different code paths.

    Returns the retired topics and the number of sessions removed with them.
    Idempotent: a second run matches nothing and reports `([], 0)`.
    """
    topics = {entry["topic"] for entry in json.loads(path.read_text())}
    async with _session(db) as db:
        cards = (await db.exec(select(Card).where(col(Card.topic).in_(topics)))).all()
        card_ids = [card.id for card in cards]
        sessions = (await db.exec(select(Session).where(col(Session.card_id).in_(card_ids)))).all()
        captures = (
            await db.exec(
                select(PendingCapture).where(col(PendingCapture.activated_card_id).in_(card_ids))
            )
        ).all()
        retired = sorted(card.topic for card in cards)
        if dry_run:
            return retired, len(sessions)

        for session in sessions:
            await db.delete(session)
        for capture in captures:
            await db.delete(capture)
        # Flush dependents before their cards. Postgres would otherwise cascade
        # the rows first and leave SQLAlchemy warning that its queued explicit
        # delete matched nothing; SQLite still needs the explicit deletes.
        await db.flush()
        for card in cards:
            await db.delete(card)
        await db.commit()
        return retired, len(sessions)


def _fixtures() -> list[dict]:
    """The prototype's three cards, verbatim from `Devmax.dc.html`."""
    return [
        {
            "topic": "Consistent hashing",
            "category": "Core Concept",
            "summary": "solid on ring mechanics, shaky on virtual nodes",
            "last_score": 2,
            "ease_factor": 2.36,
            "interval_days": 3,
            "repetitions": 3,
            "days_overdue": 3,
            "sessions": [
                {
                    "days_ago": 3,
                    "score": 2,
                    "feedback": "Explained the ring, stalled on virtual nodes.",
                    "question": "What problem does consistent hashing solve that modulo "
                    "hashing doesn't?",
                    "answer": "With mod-N, if you add a server the modulus changes and almost "
                    "every key maps somewhere new, so the whole cache is cold. Consistent "
                    "hashing keeps most keys where they are.",
                    "follow_up": "One more — what are virtual nodes for?",
                    "follow_up_answer": "I think they… split a node into several ring "
                    "positions? I'm not sure what that actually buys you.",
                },
                {
                    "days_ago": 8,
                    "score": 3,
                    "feedback": "Correct lookup path. No mention of how replicas are chosen "
                    "from the successors.",
                    "question": "How does a client find which node owns a key?",
                    "answer": "Hash the key, walk clockwise on the ring to the first node "
                    "position you hit, that node owns it.",
                },
                {
                    "days_ago": 12,
                    "score": 4,
                    "feedback": "Concise and accurate framing.",
                    "question": "Describe consistent hashing in two sentences.",
                    "answer": "Nodes and keys are both hashed into the same circular space, "
                    "and each key is owned by the next node clockwise. Adding or removing a "
                    "node only affects the keys in the adjacent arc.",
                },
            ],
        },
        {
            "topic": "Raft leader election",
            "category": "Distributed Systems",
            "summary": "can describe terms and voting; fuzzy on log-matching safety",
            "last_score": 1,
            "ease_factor": 1.96,
            "interval_days": 1,
            "repetitions": 0,
            "days_overdue": 1,
            "missed_count": 2,
            # Drives the `resumable` chip on the Today row and the resume banner.
            "draft": "Okay so each server has a term number, and when a follower stops "
            "hearing heartbeats it bumps its term and becomes a candidate, then it asks",
            "open_question": "A follower stops hearing heartbeats and starts an election. "
            "What stops it from becoming leader with an incomplete log?",
            "sessions": [
                {
                    "days_ago": 1,
                    "score": 1,
                    "feedback": "Terms are election epochs, not log positions. Worth "
                    "re-reading before the next review.",
                    "question": "What is a term in Raft, and what increments it?",
                    "answer": "A term is like the index of the log entry, it goes up every "
                    "time you write something.",
                },
                {
                    "days_ago": 5,
                    "score": 2,
                    "feedback": "One vote per term is right. The up-to-date-log condition was "
                    "reached only when prompted.",
                    "question": "Who can a follower vote for in a given term?",
                    "answer": "One candidate per term, first come first served I think.",
                    "follow_up": "One more — is that the only condition?",
                    "follow_up_answer": "Probably also something about the log being up to date.",
                },
            ],
        },
        {
            "topic": "Postgres index types",
            "category": "Databases",
            "summary": "confident on B-tree vs GIN; hasn't explained index bloat",
            "last_score": 3,
            "ease_factor": 2.5,
            "interval_days": 7,
            "repetitions": 2,
            "days_overdue": 0,
            "sessions": [
                {
                    "days_ago": 7,
                    "score": 3,
                    "feedback": "Correct boundary. Follow-up on bloat went unanswered.",
                    "question": "When is a B-tree index the wrong choice?",
                    "answer": "When you're not querying by an ordered scalar — full-text, "
                    "array containment, jsonb keys. B-tree needs a single comparable value.",
                },
            ],
        },
        # A desk card, so you can verify it never appears in /cards/due or a push.
        {
            "topic": "Multi-source BFS",
            "category": "Coding Pattern",
            "summary": "",
            "last_score": None,
            "ease_factor": 2.5,
            "interval_days": 1,
            "repetitions": 0,
            "days_overdue": 2,
            "sessions": [],
        },
    ]


async def load_fixtures(db: AsyncSession | None = None) -> int:
    now = datetime.now(UTC)
    added = 0
    async with _session(db) as db:
        # The configured timezone, not the container's: a UTC server would seed
        # these a day early after 17:00 PT.
        today = await local_today(db)
        for spec in _fixtures():
            if (await db.exec(select(Card).where(Card.topic == spec["topic"]))).first():
                continue
            card = Card(
                topic=spec["topic"],
                category=spec["category"],
                delivery_mode=delivery_mode_for(spec["category"]),
                mastery_summary=spec["summary"],
                last_score=spec["last_score"],
                ease_factor=spec["ease_factor"],
                interval_days=spec["interval_days"],
                repetitions=spec["repetitions"],
                missed_count=spec.get("missed_count", 0),
                next_review_at=today - timedelta(days=spec["days_overdue"]),
            )
            db.add(card)
            await db.flush()

            for s in spec["sessions"]:
                db.add(
                    Session(
                        card_id=card.id,
                        question_asked=s["question"],
                        answer_text=s["answer"],
                        follow_up_question=s.get("follow_up"),
                        follow_up_answer=s.get("follow_up_answer", ""),
                        follow_up_used=bool(s.get("follow_up")),
                        score=s["score"],
                        feedback=s["feedback"],
                        status=STATUS_COMPLETE,
                        started_at=now - timedelta(days=s["days_ago"]),
                        ended_at=now - timedelta(days=s["days_ago"]),
                    )
                )

            if spec.get("draft"):
                db.add(
                    Session(
                        card_id=card.id,
                        question_asked=spec["open_question"],
                        draft_text=spec["draft"],
                        started_at=now - timedelta(hours=14),
                    )
                )
            added += 1
        await db.commit()
    return added


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed Devmax cards")
    parser.add_argument("--file", type=Path, help="cards.json from the study plan")
    parser.add_argument(
        "--weeks-through",
        type=int,
        default=2,
        help="bulk-load cards with target_week <= N (default 2; intended for local "
        "verification and clean-room imports)",
    )
    parser.add_argument(
        "--activate-week",
        type=int,
        help="load exactly curriculum week N and schedule it from --start-date; "
        "preferred for the production learning sequence",
    )
    parser.add_argument(
        "--start-date",
        type=date.fromisoformat,
        help="YYYY-MM-DD that week 1 begins on (default: today in the configured "
        "timezone). Reuse the same value on later loads so weeks stay aligned.",
    )
    parser.add_argument(
        "--retire-file",
        type=Path,
        help="delete every card named in this manifest, and its sessions "
        "(e.g. archive/cards-legacy-126.json). Needs --dry-run or --confirm",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="with --retire-file, report what would be deleted and stop",
    )
    parser.add_argument(
        "--confirm",
        action="store_true",
        help="with --retire-file, actually delete",
    )
    parser.add_argument(
        "--fixtures",
        action="store_true",
        help="load the three design-prototype cards plus one desk card",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="allow --fixtures against a production database",
    )
    args = parser.parse_args()
    if args.activate_week is not None and args.activate_week < 1:
        parser.error("--activate-week must be at least 1")

    if args.retire_file:
        # The inverse of the --fixtures guard below: that one refuses to touch a
        # production database, whereas retiring a curriculum is *for* production.
        # So the gate is an explicit acknowledgement rather than a host check.
        if not args.dry_run and not args.confirm:
            parser.error(
                "--retire-file deletes cards and their session history. Re-run with "
                "--dry-run to see what would go, or --confirm to do it."
            )
        retired, sessions = asyncio.run(retire_from_file(args.retire_file, dry_run=args.dry_run))
        for topic in retired:
            print(f"  {topic}")
        verb = "would retire" if args.dry_run else "retired"
        print(f"{verb} {len(retired)} cards and {sessions} sessions")
    elif args.fixtures:
        # The fixtures carry invented session history and a 14-hour-old draft, which
        # would render a bogus resume banner on a real card. They exist to reproduce
        # the design screenshots, not to seed a study queue.
        #
        # Allowlist, not denylist: an unrecognised host is assumed to be real. A
        # check for one vendor's domain would wave through Railway, Supabase, RDS,
        # or a custom domain in front of the same database.
        if not is_local_database(get_settings().database_url) and not args.force:
            parser.error(
                "--fixtures targets a non-local database. Use --file cards.json "
                "instead, or pass --force if you really mean it."
            )
        print(f"seeded {asyncio.run(load_fixtures())} fixture cards")
    elif args.file:
        added = asyncio.run(
            load_from_file(
                args.file,
                args.weeks_through,
                args.start_date,
                activate_week=args.activate_week,
            )
        )
        print(f"seeded {added} cards")
    else:
        parser.error("pass --fixtures or --file cards.json")


if __name__ == "__main__":
    main()
