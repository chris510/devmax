import uuid
from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


class DueCard(BaseModel):
    id: uuid.UUID
    topic: str
    category: str
    mastery_summary: str
    last_score: int | None
    due_label: str
    resumable: bool
    missed_count: int


class CardSummary(BaseModel):
    id: uuid.UUID
    topic: str
    category: str
    delivery_mode: str
    mastery_summary: str
    last_score: int | None
    ease_factor: float
    interval_days: int
    repetitions: int
    next_review_at: date
    missed_count: int


class Turn(BaseModel):
    role: Literal["question", "answer", "follow_up", "score"]
    text: str


class SessionHistory(BaseModel):
    id: uuid.UUID
    date: datetime
    score: int | None
    feedback: str
    turns: list[Turn]


class CardDetail(CardSummary):
    sessions: list[SessionHistory]


class TierCard(BaseModel):
    id: uuid.UUID
    topic: str
    mastery_summary: str
    last_score: int | None = None
    days_overdue: int | None = None


class Overview(BaseModel):
    counts: dict[str, int]
    shaky: list[TierCard]
    cold: list[TierCard]


class CreateCard(BaseModel):
    topic: str = Field(min_length=1, max_length=200)
    schedule: Literal["now", "next"] = "now"


class SessionStart(BaseModel):
    session_id: uuid.UUID
    question: str
    is_follow_up: bool
    draft_text: str
    resumed: bool


class DraftUpdate(BaseModel):
    draft_text: str = ""


class AnswerIn(BaseModel):
    text: str = ""


class FollowUpOut(BaseModel):
    status: Literal["follow_up"] = "follow_up"
    question: str


class CompleteOut(BaseModel):
    status: Literal["complete"] = "complete"
    score: int
    feedback: str
    next_review_at: date
    interval_days: int


class DeviceTokenIn(BaseModel):
    token: str = Field(min_length=1)
    kind: str = "apns"


class NotificationWindow(BaseModel):
    label: str
    on: bool
    # `from` is a Python keyword, so the field is aliased to match the wire format.
    from_: str = Field(alias="from")
    to: str

    model_config = {"populate_by_name": True}


class SettingsOut(BaseModel):
    reviews_per_day: int = Field(ge=1, le=6)
    timezone: str
    windows: list[NotificationWindow]


class SettingsIn(SettingsOut):
    pass


class TriggerResult(BaseModel):
    sent: bool
    reason: str | None = None
    card_id: uuid.UUID | None = None
    due_count: int | None = None


def window_to_dict(w: NotificationWindow) -> dict[str, Any]:
    return {"label": w.label, "from": w.from_, "to": w.to, "on": w.on}
