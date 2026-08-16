"""Pure card-derived values. No request context, so all directly unit-testable."""

from collections.abc import Sequence
from datetime import UTC, date, datetime, time, timedelta, tzinfo

from app.models import Card, Session, SessionProbe
from app.schemas import Turn
from app.services.scoring_contract import active_card_score

UNTESTED = "untested"
SHAKY = "shaky"
DEVELOPING = "developing"
SOLID = "solid"
COLD = "cold"

TIERS = (UNTESTED, SHAKY, DEVELOPING, SOLID, COLD)

# A new local day alone is not separation: opening Learn at 23:59 must not make
# an answer at 00:01 look like recall. Eight elapsed hours plus the day boundary
# keeps the gate honest while still making a daytime lesson available next morning.
MIN_LEARNING_RECALL_DELAY = timedelta(hours=8)


def _as_utc(value: datetime) -> datetime:
    """SQLite drops tzinfo from timestamptz values; those values are stored as UTC."""
    aware = value if value.tzinfo else value.replace(tzinfo=UTC)
    return aware.astimezone(UTC)


def learning_recall_available_at(local_now: datetime) -> datetime:
    """The later of next local midnight and eight real elapsed hours, in UTC."""
    now_utc = _as_utc(local_now)
    next_midnight = datetime.combine(
        local_now.date() + timedelta(days=1), time.min, tzinfo=local_now.tzinfo
    ).astimezone(UTC)
    return max(next_midnight, now_utc + MIN_LEARNING_RECALL_DELAY)


def learning_exposure_boundary(
    local_now: datetime,
    *,
    existing_recall_not_before_at: datetime | None = None,
) -> tuple[datetime, datetime]:
    """Return one monotonic exposure instant and recall gate, both in UTC.

    Every endpoint that reveals learning authority must use this calculation.
    The exposure timestamp records the current disclosure while a pre-existing
    later gate is preserved, so a replay can extend but never shorten the hold.
    """
    exposed_at = _as_utc(local_now)
    computed_gate = learning_recall_available_at(local_now)
    existing_gate = (
        _as_utc(existing_recall_not_before_at)
        if existing_recall_not_before_at is not None
        else None
    )
    return exposed_at, max(
        value for value in (existing_gate, computed_gate) if value is not None
    )


def recall_is_available(card: Card, at: datetime) -> bool:
    return (
        card.recall_not_before_at is None
        or _as_utc(card.recall_not_before_at) <= _as_utc(at)
    )


def effective_review_date(
    card: Card, tz: tzinfo, *, at: datetime | None = None
) -> date:
    """Visible due date while held, without rewriting the scheduler-owned date."""
    gate = card.recall_not_before_at
    now = at or datetime.now(UTC)
    if gate is None or _as_utc(gate) <= _as_utc(now):
        return card.next_review_at
    gate_date = _as_utc(gate).astimezone(tz).date()
    return max(card.next_review_at, gate_date)


def due_label(next_review_at: date, today: date) -> str:
    """Computed server-side so the client never reimplements date math."""
    days = (today - next_review_at).days
    if days < 0:
        ahead = -days
        return "due tomorrow" if ahead == 1 else f"due in {ahead} days"
    if days == 0:
        return "due today"
    if days == 1:
        return "1 day overdue"
    return f"{days} days overdue"


def days_since_review(card: Card, today: date, tz: tzinfo) -> int | None:
    """None until the card has been answered once.

    `last_reviewed_at` is stored in UTC and `today` is a local calendar day, so
    the timestamp is converted before its date is taken. Otherwise a card
    reviewed this evening reads as reviewed tomorrow.
    """
    if card.last_reviewed_at is None:
        return None
    return (today - card.last_reviewed_at.astimezone(tz).date()).days


def classify_tier(card: Card, today: date) -> str:
    """Derived, never stored. Evaluation order matters; see spec.md §/cards/overview.

    `cold` is checked first because it overrides `solid`: "never learned it" and
    "knew it cold three weeks ago and let it lapse" are different problems, and
    nothing else in the API distinguishes them.
    """
    score = active_card_score(card)
    is_solid = card.repetitions >= 3 and card.ease_factor >= 2.5 and (score or 0) >= 4
    lapse_cutoff = (today - card.next_review_at).days > 2 * card.interval_days
    if is_solid and lapse_cutoff:
        return COLD
    if (score is not None and score <= 2) or card.ease_factor < 2.0:
        return SHAKY
    if card.repetitions == 0:
        return UNTESTED
    if card.repetitions in (1, 2) and score is not None and 3 <= score <= 4:
        return DEVELOPING
    if is_solid:
        return SOLID
    return DEVELOPING


def build_turns(session: Session, probes: Sequence[SessionProbe]) -> list[Turn]:
    """Assemble transcript ordering server-side. The client must not do this.

    `probes` is this session's scored follow-ups in `idx` order, and each one
    contributes the pair the transcript already had. A two-probe session reads
    question, answer, follow-up, answer, follow-up, answer, score. The caller
    passes them in because loading them per session would be an N+1 on a card with
    a long history.
    """
    turns = [Turn(role="question", text=session.question_asked)]
    if session.answer_text:
        turns.append(Turn(role="answer", text=session.answer_text))
    for probe in probes:
        turns.append(Turn(role="follow_up", text=probe.question))
        if probe.answer:
            turns.append(Turn(role="answer", text=probe.answer))
    if session.score is not None:
        turns.append(Turn(role="score", text=f"{session.score} · {session.feedback}"))
    return turns
