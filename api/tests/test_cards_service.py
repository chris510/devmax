from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from app.config import get_settings
from app.services.cards import (
    COLD,
    DEVELOPING,
    SHAKY,
    SOLID,
    UNTESTED,
    classify_tier,
    days_since_review,
    due_label,
    effective_review_date,
    learning_recall_available_at,
    recall_is_available,
)
from tests.conftest import make_card

TODAY = date(2026, 7, 24)


@pytest.mark.parametrize(
    ("offset", "expected"),
    [
        (-2, "due in 2 days"),
        (-1, "due tomorrow"),
        (0, "due today"),
        (1, "1 day overdue"),
        (3, "3 days overdue"),
    ],
)
def test_due_label(offset, expected):
    assert due_label(TODAY - timedelta(days=offset), TODAY) == expected


def test_untested_when_never_reviewed():
    """repetitions == 0 with a null score is untested, not shaky."""
    card = make_card(repetitions=0, last_score=None, ease_factor=2.5)
    assert classify_tier(card, TODAY) == UNTESTED


def test_shaky_on_low_score():
    assert classify_tier(make_card(repetitions=2, last_score=2), TODAY) == SHAKY


def test_shaky_on_low_ease_factor():
    card = make_card(repetitions=4, last_score=4, ease_factor=1.8)
    assert classify_tier(card, TODAY) == SHAKY


def test_developing():
    assert classify_tier(make_card(repetitions=2, last_score=4), TODAY) == DEVELOPING


def test_solid():
    card = make_card(repetitions=4, last_score=5, ease_factor=2.6, next_review_at=TODAY)
    assert classify_tier(card, TODAY) == SOLID


def test_cold_beats_solid():
    """A card that qualifies as both solid and cold must classify as cold.

    "Never learned it" and "knew it cold three weeks ago and let it lapse" are
    different problems, and nothing else in the API distinguishes them.
    """
    card = make_card(
        repetitions=4,
        last_score=5,
        ease_factor=2.6,
        interval_days=7,
        next_review_at=TODAY - timedelta(days=15),  # > 2 * interval_days
    )
    assert classify_tier(card, TODAY) == COLD


def test_lapsed_but_not_solid_is_not_cold():
    card = make_card(
        repetitions=2,
        last_score=4,
        ease_factor=2.5,
        interval_days=7,
        next_review_at=TODAY - timedelta(days=30),
    )
    assert classify_tier(card, TODAY) == DEVELOPING


def test_v2_tiering_uses_recall_instead_of_the_legacy_composite(monkeypatch):
    monkeypatch.setattr(get_settings(), "scoring_contract_version", 2)
    card = make_card(
        repetitions=4,
        last_score=5,
        last_accuracy=2,
        ease_factor=2.6,
        next_review_at=TODAY,
    )
    assert classify_tier(card, TODAY) == SHAKY


# --- days since review ------------------------------------------------------
# `last_reviewed_at` is stored in UTC; `today` is the user's local calendar day.
# West of UTC those two disagree for the whole evening, which is exactly when a
# session gets answered.

LA = ZoneInfo("America/Los_Angeles")


def test_learning_at_midday_waits_until_the_next_local_day():
    exposed = datetime(2026, 8, 11, 12, 0, tzinfo=LA)
    expected = datetime(2026, 8, 12, 0, 0, tzinfo=LA).astimezone(UTC)

    assert learning_recall_available_at(exposed) == expected


def test_learning_near_midnight_still_waits_eight_elapsed_hours():
    exposed = datetime(2026, 8, 11, 23, 55, tzinfo=LA)

    assert learning_recall_available_at(exposed) == exposed.astimezone(
        UTC
    ) + timedelta(hours=8)


def test_learning_gate_uses_a_real_local_midnight_across_dst():
    new_york = ZoneInfo("America/New_York")
    # This instant is one hour before spring-forward. The next midnight has the
    # new UTC offset, so adding a fixed 24-hour UTC day would be wrong.
    exposed = datetime(2027, 3, 14, 1, 0, tzinfo=new_york)
    expected = datetime(2027, 3, 15, 0, 0, tzinfo=new_york).astimezone(UTC)

    assert learning_recall_available_at(exposed) == expected


def test_recall_gate_is_inclusive_and_does_not_rewrite_the_sm2_date():
    gate = datetime(2026, 8, 12, 7, 0, tzinfo=UTC)
    card = make_card(
        next_review_at=date(2026, 8, 1),
        recall_not_before_at=gate,
    )

    assert recall_is_available(card, gate - timedelta(microseconds=1)) is False
    assert recall_is_available(card, gate) is True
    assert effective_review_date(
        card, LA, at=gate - timedelta(microseconds=1)
    ) == date(2026, 8, 12)
    assert effective_review_date(card, LA, at=gate) == date(2026, 8, 1)
    assert card.next_review_at == date(2026, 8, 1)


def test_days_since_review_is_none_until_the_card_has_been_answered():
    assert days_since_review(make_card(), TODAY, LA) is None


def test_an_evening_review_is_zero_days_ago_not_minus_one():
    """The regression: 24 Jul 19:00 in Los Angeles is already 25 Jul in UTC.

    Taking `.date()` off the stored UTC timestamp and subtracting it from the
    local day returned -1, and Coverage rendered `-1D SINCE REVIEW` for a card
    reviewed minutes earlier. Only reproducible for part of the day, so it has
    to be pinned rather than left to whenever CI happens to run.
    """
    reviewed = datetime(2026, 7, 24, 19, 30, tzinfo=LA)
    assert reviewed.astimezone(UTC).date() == date(2026, 7, 25)  # the trap

    card = make_card(last_reviewed_at=reviewed.astimezone(UTC))
    assert days_since_review(card, TODAY, LA) == 0


def test_days_since_review_counts_whole_local_days():
    card = make_card(last_reviewed_at=datetime(2026, 7, 15, 8, 0, tzinfo=LA).astimezone(UTC))
    assert days_since_review(card, TODAY, LA) == 9
