from datetime import date, timedelta

import pytest

from app.services.scheduler import (
    EASE_CAP,
    EASE_FLOOR,
    RATING_AGAIN,
    RATING_GOOD,
    RATING_QUALITY,
    apply_sm2,
    quality_for,
    rating_for,
)

TODAY = date(2026, 7, 24)


def test_first_successful_review_uses_one_day():
    ease, interval, reps, next_at = apply_sm2(2.5, 1, 0, 4, TODAY)
    assert (interval, reps) == (1, 1)
    assert next_at == TODAY + timedelta(days=1)
    assert ease == pytest.approx(2.5)


def test_second_successful_review_jumps_to_six_days():
    _, interval, reps, next_at = apply_sm2(2.5, 1, 1, 4, TODAY)
    assert (interval, reps) == (6, 2)
    assert next_at == TODAY + timedelta(days=6)


def test_third_review_multiplies_by_ease_factor():
    _, interval, reps, _ = apply_sm2(2.5, 6, 2, 4, TODAY)
    assert (interval, reps) == (15, 3)


def test_interval_multiplication_rounds():
    _, interval, _, _ = apply_sm2(2.36, 3, 3, 4, TODAY)
    assert interval == round(3 * 2.36)


def test_failure_resets_repetitions_and_interval():
    ease, interval, reps, next_at = apply_sm2(2.5, 15, 4, 1, TODAY)
    assert (interval, reps) == (1, 0)
    assert next_at == TODAY + timedelta(days=1)
    assert ease < 2.5


@pytest.mark.parametrize(
    ("quality", "passes"),
    [(0, False), (1, False), (2, False), (3, True), (4, True), (5, True)],
)
def test_score_two_fails_and_score_three_passes(quality, passes):
    """Both 2 and 3 trigger an in-app follow-up, but only 3 passes SM-2.

    These are two independent thresholds and must not be collapsed.
    """
    _, interval, reps, _ = apply_sm2(2.5, 6, 2, quality, TODAY)
    if passes:
        assert reps == 3 and interval > 1
    else:
        assert (reps, interval) == (0, 1)


def test_perfect_recall_raises_ease_factor():
    ease, _, _, _ = apply_sm2(2.5, 6, 2, 5, TODAY)
    assert ease == pytest.approx(2.6)


def test_ease_factor_floors_at_1_3():
    ease = 1.3
    for _ in range(10):
        ease, _, _, _ = apply_sm2(ease, 1, 0, 0, TODAY)
    assert ease == EASE_FLOOR


def test_ease_factor_caps_at_3_0():
    ease = 2.5
    for _ in range(20):
        ease, _, _, _ = apply_sm2(ease, 6, 5, 5, TODAY)
    assert ease == EASE_CAP


def test_quality_outside_zero_to_five_is_rejected():
    with pytest.raises(ValueError):
        apply_sm2(2.5, 1, 0, 6, TODAY)


def test_full_multi_review_sequence():
    """Learn it, lapse on it, relearn it — intervals and reps track correctly."""
    ease, interval, reps, today = 2.5, 1, 0, TODAY

    for quality, expected_interval, expected_reps in [
        (4, 1, 1),
        (4, 6, 2),
        (5, 15, 3),
        (1, 1, 0),  # lapse: back to square one, ease damaged
        (3, 1, 1),
        (4, 6, 2),
    ]:
        ease, interval, reps, today = apply_sm2(ease, interval, reps, quality, today)
        assert (interval, reps) == (expected_interval, expected_reps)

    # The lapse left a permanent mark on the ease factor.
    assert ease < 2.5


# --- the mechanism gate -----------------------------------------------------
# What the scheduler is handed is no longer the displayed score. `rating_for`
# collapses a session to two buckets from mechanism accuracy alone.


@pytest.mark.parametrize(
    ("mechanism", "expected"),
    [
        (0, RATING_AGAIN),
        (1, RATING_AGAIN),
        (2, RATING_AGAIN),
        (3, RATING_GOOD),
        (4, RATING_GOOD),
        (5, RATING_GOOD),
    ],
)
def test_rating_splits_at_the_mechanism_pass_mark(mechanism: int, expected: str):
    assert rating_for(mechanism) == expected


def test_every_passing_mechanism_produces_the_same_schedule():
    """Two buckets, not six — depth beyond the mechanism must not move the card."""
    outcomes = {apply_sm2(2.5, 6, 2, quality_for(m), TODAY) for m in (3, 4, 5)}
    assert len(outcomes) == 1


def test_every_failing_mechanism_produces_the_same_schedule():
    outcomes = {apply_sm2(2.5, 15, 4, quality_for(m), TODAY) for m in (0, 1, 2)}
    assert len(outcomes) == 1


def test_a_pass_is_ease_neutral_and_a_lapse_costs_ease():
    passed, _, _, _ = apply_sm2(2.5, 6, 2, quality_for(3), TODAY)
    lapsed, _, _, _ = apply_sm2(2.5, 6, 2, quality_for(2), TODAY)
    assert passed == pytest.approx(2.5)
    assert lapsed < 2.5
    assert lapsed >= EASE_FLOOR


def test_the_gate_quality_values_stay_inside_sm2s_range():
    for quality in RATING_QUALITY.values():
        assert 0 <= quality <= 5
