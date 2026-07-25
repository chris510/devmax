"""SM-2 scheduling. See spec.md §SM-2 implementation.

Pure functions, independent of FastAPI request context, so they're directly
unit-testable. This is the one piece of logic that must be exactly right.
"""

from datetime import date, timedelta

EASE_FLOOR = 1.3
EASE_CAP = 3.0
# SM-2's pass/fail threshold. Distinct from the app's follow-up threshold
# (scores 2 and 3 both trigger a follow-up) — two independent thresholds.
PASS_QUALITY = 3


def apply_sm2(
    ease_factor: float,
    interval_days: int,
    repetitions: int,
    quality: int,
    today: date,
) -> tuple[float, int, int, date]:
    """Returns (new_ease_factor, new_interval_days, new_repetitions, next_review_at).

    ``quality`` is the FINAL session score, after any follow-up: a session that
    scored 2, got a follow-up, and ended at 4 feeds SM-2 a 4.

    ``missed_count`` deliberately never reaches this function. Missing a review is
    a compliance signal, not a retention signal — conflating them would let a busy
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
