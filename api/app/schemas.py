import uuid
from datetime import date, datetime, time
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator


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
    # The three axes behind `last_score`. Coverage's rollup means these across the
    # library; nothing else consumes them.
    last_mechanism_accuracy: int | None = None
    last_trade_off_awareness: int | None = None
    last_failure_mode_awareness: int | None = None
    ease_factor: float
    interval_days: int
    repetitions: int
    next_review_at: date
    # Both computed server-side so the client never reimplements date math.
    due_label: str
    days_since_review: int | None
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
    # A practice run left the schedule alone; the two fields above are the card's
    # unchanged values, and the session-end line says so instead of quoting them.
    practice: bool = False
    # Whether the client should offer the coached re-attempt. Server-computed so the
    # `mechanism_accuracy <= 2` gate lives in one place and the app never sees a
    # per-axis score it has nowhere to display.
    reattempt_offered: bool = False
    # The exact prompt to show for turn 3, or null when it isn't offered. Sent by the
    # server for the same reason turns 1 and 2 are: it is the question the answer
    # will be graded against, and the client cannot reliably reconstruct it — on a
    # resumed follow-up session the client's copy of "the question" is the probe.
    reattempt_prompt: str | None = None


class ReattemptOut(BaseModel):
    """Deliberately carries no score.

    Turn 3's `mechanism_accuracy` is stored but never returned — the app shows one
    numeral per session and that numeral is turn 2's composite. Sending a second
    number to a client that has no place to put it invites putting it somewhere.
    """

    mastery_summary: str


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


# A window shorter than the cron's poll interval can fall between two polls and
# never fire, so this is the floor. Pinned against the actual schedule in
# .github/workflows/trigger-review.yml by
# `test_the_minimum_window_is_at_least_the_poll_interval` — a comment alone is
# the same hand-synced coupling this whole change set exists to remove.
MIN_WINDOW_MINUTES = 30


def _minutes(t: time) -> int:
    return t.hour * 60 + t.minute


class NotificationWindowIn(NotificationWindow):
    """A notification window on the write path, where the rules apply.

    Deliberately a separate model from `NotificationWindow`: `read_settings`
    rebuilds that one from stored JSON, so a rule there would make
    `GET /settings` fail on any row written before the rule existed — including
    the hand-widened windows docs/RUNBOOK.md describes for testing a push. Reads
    stay permissive; writes are constrained.

    Validating per window rather than across the whole settings object also puts
    the offending index in the 422's `loc`, so a client can point at the window
    it rejected instead of the request body as a whole.
    """

    @model_validator(mode="after")
    def _usable(self) -> "NotificationWindowIn":
        try:
            span = _minutes(time.fromisoformat(self.to)) - _minutes(time.fromisoformat(self.from_))
        except ValueError as exc:
            raise ValueError("times must be 24-hour HH:MM") from exc
        if span < MIN_WINDOW_MINUTES:
            # A non-positive span covers `to` before `from`, and `to == from` —
            # both two taps away in the app, which advances each end separately.
            raise ValueError(
                f"a window must run at least {MIN_WINDOW_MINUTES} minutes; this one is {span}"
            )
        return self


class SettingsIn(SettingsOut):
    windows: list[NotificationWindowIn]


class TriggerResult(BaseModel):
    sent: bool
    # Constrained because this is what the cron consumer branches on — a typo'd
    # reason should fail here, not read as an unrecognised-but-valid state.
    reason: (
        Literal[
            "outside_window",
            # This window already produced a push...
            "already_pushed",
            # ...versus: every due card was already offered earlier today. Two
            # different facts, kept apart so a log tells you which one happened.
            "already_offered",
            "daily_limit",
            "nothing_due",
            "no_devices",
        ]
        | None
    ) = None
    card_id: uuid.UUID | None = None
    due_count: int | None = None


def window_to_dict(w: NotificationWindow) -> dict[str, Any]:
    return {"label": w.label, "from": w.from_, "to": w.to, "on": w.on}
