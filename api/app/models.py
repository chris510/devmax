import uuid
from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import JSON, CheckConstraint, Column, DateTime, Index, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, SQLModel

# Postgres in production; the variant keeps the SQLite test schema compilable.
WINDOWS_TYPE = JSONB().with_variant(JSON(), "sqlite")

# Every timestamp column is `timestamptz` in migration 0001 and every value written
# is tz-aware (`_now()` below). A bare `datetime` annotation would map to a naive
# DateTime, and the asyncpg dialect casts bind parameters from the *model* type — so
# it would emit `$n::TIMESTAMP WITHOUT TIME ZONE` and asyncpg would reject the aware
# value with a DataError on every insert. SQLite silently drops tzinfo instead, which
# is why this only ever surfaces against Postgres.
TZ_DATETIME = DateTime(timezone=True)

DELIVERY_CONVERSATIONAL = "conversational"
DELIVERY_DESK = "desk"

STATUS_OPEN = "open"
STATUS_AWAITING_FOLLOW_UP = "awaiting_follow_up"
STATUS_COMPLETE = "complete"
STATUS_ABANDONED = "abandoned"

# A card is resumable only while its session is still in one of these states.
LIVE_STATUSES = (STATUS_OPEN, STATUS_AWAITING_FOLLOW_UP)

DEFAULT_WINDOWS: list[dict[str, Any]] = [
    {"label": "Morning", "from": "07:10", "to": "08:30", "on": True},
    {"label": "Evening", "from": "21:00", "to": "22:30", "on": True},
]


def _now() -> datetime:
    return datetime.now(UTC)


class Card(SQLModel, table=True):
    __tablename__ = "cards"
    __table_args__ = (
        Index("ix_cards_next_review_at", "next_review_at"),
        # The hot query: due conversational cards. Desk cards never enter the push loop.
        Index(
            "ix_cards_due_conversational",
            "next_review_at",
            postgresql_where=text("delivery_mode = 'conversational'"),
        ),
    )

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    topic: str
    category: str
    pattern: str | None = None
    source_company: str | None = None
    target_week: int | None = None
    delivery_mode: str = DELIVERY_CONVERSATIONAL

    # Generated once, then reused for every session on this card. Repeating the same
    # retrieval is the point — a fresh question each time puts every review in the
    # weak-transfer regime. Null until the first session generates one.
    canonical_question: str | None = None

    ease_factor: float = 2.5
    interval_days: int = 1
    repetitions: int = 0
    next_review_at: date
    last_score: int | None = None
    # The three axes behind `last_score`, denormalised from the latest complete
    # session the same way `last_score` is. Coverage's axis rollup is a mean across
    # cards, so it needs the per-card latest value, not a scan of every session.
    last_mechanism_accuracy: int | None = None
    last_trade_off_awareness: int | None = None
    last_failure_mode_awareness: int | None = None
    last_reviewed_at: datetime | None = Field(default=None, sa_type=TZ_DATETIME)
    mastery_summary: str = ""
    # Compliance signal only — never feeds SM-2.
    missed_count: int = 0
    last_pushed_at: datetime | None = Field(default=None, sa_type=TZ_DATETIME)

    created_at: datetime = Field(default_factory=_now, sa_type=TZ_DATETIME)
    updated_at: datetime = Field(default_factory=_now, sa_type=TZ_DATETIME)


class Session(SQLModel, table=True):
    __tablename__ = "sessions"
    __table_args__ = (Index("ix_sessions_card_started", "card_id", text("started_at DESC")),)

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    card_id: uuid.UUID = Field(foreign_key="cards.id", ondelete="CASCADE")

    question_asked: str
    follow_up_question: str | None = None
    answer_text: str = ""
    follow_up_answer: str = ""
    # In-progress, unsubmitted. Losing this is the worst failure mode in the product.
    draft_text: str = ""

    # Composite, derived in code from the three axes below — never model-produced.
    # Null on rows written before the decomposition shipped; those keep their
    # original blended score for history display.
    score: int | None = None
    mechanism_accuracy: int | None = None
    trade_off_awareness: int | None = None
    failure_mode_awareness: int | None = None
    feedback: str = ""
    follow_up_used: bool = False
    # A Review Sprint run. Scored and written to history exactly like a normal
    # session; the card's SM-2 fields are left untouched.
    practice: bool = False
    status: str = STATUS_OPEN

    started_at: datetime = Field(default_factory=_now, sa_type=TZ_DATETIME)
    ended_at: datetime | None = Field(default=None, sa_type=TZ_DATETIME)


class DeviceToken(SQLModel, table=True):
    __tablename__ = "device_tokens"

    token: str = Field(primary_key=True)
    kind: str = "apns"
    created_at: datetime = Field(default_factory=_now, sa_type=TZ_DATETIME)


class Settings(SQLModel, table=True):
    __tablename__ = "settings"
    __table_args__ = (CheckConstraint("id = 1", name="ck_settings_singleton"),)

    id: int = Field(default=1, primary_key=True)
    reviews_per_day: int = 2
    windows: list[dict[str, Any]] = Field(
        default_factory=lambda: list(DEFAULT_WINDOWS),
        sa_column=Column(WINDOWS_TYPE, nullable=False),
    )
    timezone: str = "America/Los_Angeles"
