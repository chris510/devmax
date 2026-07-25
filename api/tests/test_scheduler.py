from datetime import date, timedelta

import pytest

from app.services.scheduler import EASE_CAP, EASE_FLOOR, apply_sm2

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
