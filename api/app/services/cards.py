"""Pure card-derived values. No request context, so all directly unit-testable."""

from datetime import date

from app.models import Card, Session
from app.schemas import Turn

UNTESTED = "untested"
SHAKY = "shaky"
DEVELOPING = "developing"
SOLID = "solid"
COLD = "cold"

TIERS = (UNTESTED, SHAKY, DEVELOPING, SOLID, COLD)


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


def days_since_review(card: Card, today: date) -> int | None:
    """None until the card has been answered once."""
    if card.last_reviewed_at is None:
        return None
    return (today - card.last_reviewed_at.date()).days


def classify_tier(card: Card, today: date) -> str:
    """Derived, never stored. Evaluation order matters — see spec.md §/cards/overview.

    `cold` is checked first because it overrides `solid`: "never learned it" and
    "knew it cold three weeks ago and let it lapse" are different problems, and
    nothing else in the API distinguishes them.
    """
    is_solid = card.repetitions >= 3 and card.ease_factor >= 2.5 and (card.last_score or 0) >= 4
    lapse_cutoff = (today - card.next_review_at).days > 2 * card.interval_days
    if is_solid and lapse_cutoff:
        return COLD
    if (card.last_score is not None and card.last_score <= 2) or card.ease_factor < 2.0:
        return SHAKY
    if card.repetitions == 0:
        return UNTESTED
    if card.repetitions in (1, 2) and card.last_score is not None and 3 <= card.last_score <= 4:
        return DEVELOPING
    if is_solid:
        return SOLID
    return DEVELOPING


def build_turns(session: Session) -> list[Turn]:
    """Assemble transcript ordering server-side — the client must not do this."""
    turns = [Turn(role="question", text=session.question_asked)]
    if session.answer_text:
        turns.append(Turn(role="answer", text=session.answer_text))
    if session.follow_up_question:
        turns.append(Turn(role="follow_up", text=session.follow_up_question))
        if session.follow_up_answer:
            turns.append(Turn(role="answer", text=session.follow_up_answer))
    if session.score is not None:
        turns.append(Turn(role="score", text=f"{session.score} — {session.feedback}"))
    return turns
