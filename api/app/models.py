import uuid
from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import JSON, CheckConstraint, Column, Index, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, SQLModel

# Postgres in production; the variant keeps the SQLite test schema compilable.
WINDOWS_TYPE = JSONB().with_variant(JSON(), "sqlite")

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

    ease_factor: float = 2.5
    interval_days: int = 1
    repetitions: int = 0
    next_review_at: date
    last_score: int | None = None
    mastery_summary: str = ""
    # Compliance signal only — never feeds SM-2.
    missed_count: int = 0
    last_pushed_at: datetime | None = None

    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)


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

    score: int | None = None
    feedback: str = ""
    follow_up_used: bool = False
    status: str = STATUS_OPEN

    started_at: datetime = Field(default_factory=_now)
    ended_at: datetime | None = None


class DeviceToken(SQLModel, table=True):
    __tablename__ = "device_tokens"

    token: str = Field(primary_key=True)
    kind: str = "apns"
    created_at: datetime = Field(default_factory=_now)


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
