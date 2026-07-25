from datetime import date, timedelta

import pytest

from app.services.cards import COLD, DEVELOPING, SHAKY, SOLID, UNTESTED, classify_tier, due_label
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
