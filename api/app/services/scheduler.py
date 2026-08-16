"""SM-2 scheduling. See spec.md §SM-2 implementation.

Pure functions, independent of FastAPI request context, so they're directly
unit-testable. This is the one piece of logic that must be exactly right.
"""

from datetime import date, timedelta

EASE_FLOOR = 1.3
EASE_CAP = 3.0
# SM-2's pass/fail threshold. Distinct from the app's follow-up threshold
# (scores 2 and 3 both trigger a follow-up): two independent thresholds.
PASS_QUALITY = 3

RATING_AGAIN = "again"
RATING_GOOD = "good"
# Scheduling gates on Accuracy alone. Missing depth or boundaries is a coaching
# gap; getting the essential concept wrong is a retention failure.
ACCURACY_PASS = 3
# Two buckets, not six. The composite score is a display concern and no longer
# reaches this module. Feeding its full 0-5 range into the ease-factor delta
# would let "knew it, didn't volunteer the failure modes" move the interval.
# `again` maps to the mildest failing quality and `good` to the neutral one
# (delta 0.0), so a lapse costs ease and a pass holds it steady.
RATING_QUALITY = {RATING_AGAIN: 2, RATING_GOOD: 4}


def rating_for(accuracy: int) -> str:
    """The scheduler's view of a session: did they reconstruct the mechanism?"""
    return RATING_GOOD if accuracy >= ACCURACY_PASS else RATING_AGAIN


def quality_for(accuracy: int) -> int:
    """SM-2 quality for a session, gated on Accuracy alone."""
    return RATING_QUALITY[rating_for(accuracy)]


def apply_sm2(
    ease_factor: float,
    interval_days: int,
    repetitions: int,
    quality: int,
    today: date,
) -> tuple[float, int, int, date]:
    """Returns (new_ease_factor, new_interval_days, new_repetitions, next_review_at).

    ``quality`` comes from ``quality_for``, which derives it from the FINAL
    session's ``accuracy`` after any follow-up. It is not the composite
    score the app displays. See the RATING_QUALITY note above.

    ``missed_count`` deliberately never reaches this function. Missing a review is
    a compliance signal, not a retention signal. Conflating them would let a busy
    week trash the ease factor on topics the user knows cold.
    """
    if not 0 <= quality <= 5:
        raise ValueError(f"quality must be 0-5, got {quality}")

    if quality >= PASS_QUALITY:
        if repetitions == 0:
            new_interval = 1
        elif repetitions == 1:
            new_interval = 6
        else:
            new_interval = round(interval_days * ease_factor)
        new_repetitions = repetitions + 1
    else:
        new_interval = 1
        new_repetitions = 0

    delta = 0.1 - (5 - quality) * (0.08 + (5 - quality) * 0.02)
    new_ease = min(EASE_CAP, max(EASE_FLOOR, ease_factor + delta))

    return new_ease, new_interval, new_repetitions, today + timedelta(days=new_interval)
