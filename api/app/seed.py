"""Load cards into the database.

Two sources:

* ``cards.json`` — the 111-card study plan (spec.md §Seeding). Not yet present in
  the repo; pass ``--file`` once it lands.
* ``--fixtures`` — the three cards from the design prototype, with their real
  session history. Every screenshot in the design handoff depicts these, so this
  is what makes the designs reproducible against a real server during dev.

    uv run python -m app.seed --fixtures
    uv run python -m app.seed --file cards.json --weeks-through 2
"""

import argparse
import asyncio
import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from sqlmodel import select

from app.db import session_factory
from app.models import (
    DELIVERY_CONVERSATIONAL,
    DELIVERY_DESK,
    STATUS_COMPLETE,
    Card,
    Session,
)

# Coding problems need a keyboard and an hour, not a two-minute voice session.
DESK_CATEGORIES = {"Coding Warmup", "Coding Pattern", "Tier 2 Practical Build"}


def delivery_mode_for(category: str) -> str:
    return DELIVERY_DESK if category in DESK_CATEGORIES else DELIVERY_CONVERSATIONAL


async def load_from_file(path: Path, weeks_through: int) -> int:
    entries = json.loads(path.read_text())
    today = date.today()
    added = 0
    async with session_factory() as db:
        for entry in entries:
            week = entry.get("target_week")
            if week is not None and week > weeks_through:
                continue
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
                    target_week=week,
                    delivery_mode=delivery_mode_for(category),
                    next_review_at=today,
                )
            )
            added += 1
        await db.commit()
    return added


def _fixtures() -> list[dict]:
    """The prototype's three cards, verbatim from `Warm Cache.dc.html`."""
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


async def load_fixtures() -> int:
    today = date.today()
    now = datetime.now(UTC)
    added = 0
    async with session_factory() as db:
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
    parser = argparse.ArgumentParser(description="Seed Warm Cache cards")
    parser.add_argument("--file", type=Path, help="cards.json from the study plan")
    parser.add_argument(
        "--weeks-through",
        type=int,
        default=2,
        help="only load cards with target_week <= N (default 2, so day one isn't flooded)",
    )
    parser.add_argument(
        "--fixtures",
        action="store_true",
        help="load the three design-prototype cards plus one desk card",
    )
    args = parser.parse_args()

    if args.fixtures:
        print(f"seeded {asyncio.run(load_fixtures())} fixture cards")
    elif args.file:
        print(f"seeded {asyncio.run(load_from_file(args.file, args.weeks_through))} cards")
    else:
        parser.error("pass --fixtures or --file cards.json")


if __name__ == "__main__":
    main()
