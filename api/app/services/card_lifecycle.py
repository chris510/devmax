"""Grounding and lifecycle rules shared by every card-creation path.

Routers decide how a request is represented. This module decides whether trusted
material is sufficient to create an active card, and constructs that card at the
same SM-2 defaults regardless of whether it came from Capture or Study Plan.
"""

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from uuid import UUID

from app.config import get_settings
from app.models import (
    CAPTURE_ACTIVATED,
    CAPTURE_PENDING_SOURCE,
    CAPTURE_READY,
    CARD_ACTIVE,
    CARD_ARCHIVED,
    Card,
    PendingCapture,
)

RUBRIC_FIELDS = (
    "mechanism",
    "acceptable_alternative",
    "trade_off",
    "failure_mode",
    "misconception",
)

SCORING_RUBRIC_ALIASES = {
    "essential_account": "mechanism",
    "acceptable_alternative": "acceptable_alternative",
    "depth_extension": "trade_off",
    "boundary_extension": "failure_mode",
    "misconception": "misconception",
}
LEGACY_RUBRIC_ALIASES = {
    legacy: target for target, legacy in SCORING_RUBRIC_ALIASES.items()
}


class GroundingError(ValueError):
    def __init__(self, missing: list[str]) -> None:
        self.missing = missing
        super().__init__(f"grounding is incomplete: {', '.join(missing)}")


def _clean(value: str | None) -> str:
    return (value or "").strip()


def clean_rubric(value: dict[str, str] | None) -> dict[str, str]:
    """Normalize either public vocabulary to the historical internal keys."""
    raw = value or {}
    return {
        legacy: _clean(raw.get(legacy) or raw.get(LEGACY_RUBRIC_ALIASES[legacy]))
        for legacy in RUBRIC_FIELDS
    }


def scoring_rubric(value: dict[str, str] | None) -> dict[str, str]:
    """Read historical technical keys or the V2 subject-agnostic aliases.

    Stored JSON is never rewritten. The LLM sees one stable V2 vocabulary while
    activation and manual-entry clients can migrate independently.
    """
    raw = value or {}
    return {
        target: _clean(raw.get(target) or raw.get(legacy))
        for target, legacy in SCORING_RUBRIC_ALIASES.items()
    }


def storage_rubric(value: dict[str, str] | None) -> dict[str, str]:
    """Store new authority in the active contract's vocabulary.

    V1 stays byte-compatible during the dark launch. After activation, every
    card-creation path writes the subject-agnostic keys while readers continue
    accepting both forms, so existing JSON is never rewritten.
    """
    if get_settings().scoring_contract_version == 2:
        return scoring_rubric(value)
    return clean_rubric(value)


@dataclass(frozen=True)
class Grounding:
    source_url: str = ""
    source_section: str = ""
    source_label: str = ""
    answer_basis: str = ""
    answer_rubric: dict[str, str] | None = None
    canonical_question: str = ""

    def cleaned(self) -> "Grounding":
        return Grounding(
            source_url=_clean(self.source_url),
            source_section=_clean(self.source_section),
            source_label=_clean(self.source_label),
            answer_basis=_clean(self.answer_basis),
            answer_rubric=clean_rubric(self.answer_rubric),
            canonical_question=_clean(self.canonical_question),
        )

    def missing(self, *, require_question: bool = True) -> list[str]:
        value = self.cleaned()
        missing: list[str] = []
        if not (value.source_url or value.source_section or value.source_label):
            missing.append("source")
        if not value.answer_basis:
            missing.append("answer_basis")
        rubric = value.answer_rubric or {}
        missing.extend(f"answer_rubric.{field}" for field in RUBRIC_FIELDS if not rubric[field])
        if require_question and not value.canonical_question:
            missing.append("canonical_question")
        return missing

    def require_complete(self, *, require_question: bool = True) -> "Grounding":
        missing = self.missing(require_question=require_question)
        if missing:
            raise GroundingError(missing)
        return self.cleaned()


def grounding_from_capture(capture: PendingCapture) -> Grounding:
    return Grounding(
        source_url=capture.source_url,
        source_section=capture.source_section,
        source_label=capture.source_label,
        answer_basis=capture.answer_basis,
        answer_rubric=capture.answer_rubric,
        canonical_question=capture.canonical_question,
    )


def refresh_capture_status(capture: PendingCapture) -> None:
    if capture.activated_card_id is not None:
        capture.status = CAPTURE_ACTIVATED
        return
    capture.status = (
        CAPTURE_READY
        if not grounding_from_capture(capture).missing()
        else CAPTURE_PENDING_SOURCE
    )


def active_card_filter():
    """The one active-library predicate reused by every selection query."""
    return Card.lifecycle_status == CARD_ACTIVE


def build_grounded_card(
    *,
    user_id: UUID,
    topic: str,
    category: str,
    grounding: Grounding,
    today: date,
    schedule: str,
    replaces_card_id=None,
) -> Card:
    value = grounding.require_complete()
    return Card(
        user_id=user_id,
        topic=topic.strip(),
        category=category,
        canonical_question=value.canonical_question,
        source_url=value.source_url,
        source_section=value.source_section,
        source_label=value.source_label,
        answer_basis=value.answer_basis,
        answer_rubric=storage_rubric(value.answer_rubric),
        replaces_card_id=replaces_card_id,
        next_review_at=today if schedule == "now" else today + timedelta(days=1),
    )


def archive(card: Card) -> None:
    if card.lifecycle_status == CARD_ARCHIVED:
        return
    now = datetime.now(UTC)
    card.lifecycle_status = CARD_ARCHIVED
    card.archived_at = now
    card.updated_at = now


def restore(card: Card) -> None:
    if card.lifecycle_status == CARD_ACTIVE:
        return
    now = datetime.now(UTC)
    card.lifecycle_status = CARD_ACTIVE
    card.archived_at = None
    card.updated_at = now
