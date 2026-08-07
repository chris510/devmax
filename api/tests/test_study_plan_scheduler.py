"""The weekly scheduler. See docs/STUDY-PLAN-SPEC.md §Scheduler.

This is the Study Plan equivalent of `test_scheduler.py`: the highest-value test
surface in the feature, because everything the decision screens display is
derived from it and a wrong number there is a wrong number the user acts on.

The canonical Week 4 fixture from the design handoff (V3.4 §3) is reproduced
item for item, and the tests assert the exact totals the handoff displays —
660 / 420 / 720 / 660, and an overflow of 240 landing as 60 + 180. If any of
those move, either the scheduler is wrong or the design changed.
"""

from datetime import date

import pytest

from app.services.study_plan_scheduler import (
    INVALID_CORE_REMOVED,
    INVALID_DEADLINE,
    INVALID_OVER_CAPACITY,
    INVALID_UNRESOLVED,
    ITEM_COMPLETE,
    ITEM_PENDING,
    MODE_FIXED,
    PRIORITY_CORE,
    PRIORITY_OPTIONAL,
    PRIORITY_RECURRING,
    UNRESOLVED_DOES_NOT_FIT,
    UNRESOLVED_NO_ROOM,
    SchedDependency,
    SchedItem,
    SchedPlan,
    SchedWeek,
    build_proposal,
    effective_capacity_minutes,
    format_hours,
    summarise_week,
    week_end_date,
    week_start_date,
)

START = date(2026, 7, 27)  # a Monday; plan week 1 is 27 Jul – 2 Aug


# --- the canonical Week 4 fixture (V3.4 §3) ---------------------------------
#
# Senior backend interview · default 720 min/week (12h) · flexible ·
# est. completion Week 12. Four phases of three weeks each.

PHASE_OF_WEEK = {1: 1, 2: 1, 3: 1, 4: 2, 5: 2, 6: 2, 7: 3, 8: 3, 9: 3, 10: 4, 11: 4, 12: 4}


def _plan(**overrides) -> SchedPlan:
    defaults = dict(
        mode="flexible",
        start_date=START,
        default_weekly_capacity_minutes=720,
        current_week_index=4,
        deadline=None,
        revision=1,
    )
    return SchedPlan(**{**defaults, **overrides})


def _weeks(count: int = 12) -> list[SchedWeek]:
    return [SchedWeek(index=i, phase_index=PHASE_OF_WEEK[i]) for i in range(1, count + 1)]


# id, title, type, priority, estimate, status — in guide order.
WEEK4_ROWS = [
    ("L4-01", "PostgreSQL query and write paths", "learn", PRIORITY_CORE, 90, ITEM_COMPLETE),
    ("L4-02", "Redis, DynamoDB, and Cassandra", "learn", PRIORITY_CORE, 90, ITEM_COMPLETE),
    ("L4-03", "API gateway mechanics", "learn", PRIORITY_CORE, 60, ITEM_PENDING),
    ("P4-01", "Three timed coding sessions", "practice", PRIORITY_CORE, 120, ITEM_COMPLETE),
    ("P4-02", "Begin the behavioral story catalog", "practice", PRIORITY_CORE, 90, ITEM_PENDING),
    (
        "P4-03",
        "Rewrite your consistent-hashing explanation",
        "practice",
        PRIORITY_OPTIONAL,
        30,
        ITEM_PENDING,
    ),
    (
        "P4-04",
        "Read one published architecture write-up",
        "practice",
        PRIORITY_OPTIONAL,
        60,
        ITEM_PENDING,
    ),
    (
        "R4-01",
        "Closed-book explanation of the read and write paths",
        "retrieve",
        PRIORITY_RECURRING,
        30,
        ITEM_COMPLETE,
    ),
    (
        "R4-02",
        "Explain when you'd choose Cassandra over DynamoDB",
        "retrieve",
        PRIORITY_RECURRING,
        30,
        ITEM_COMPLETE,
    ),
    (
        "R4-03",
        "Redraw the gateway request flow from memory",
        "retrieve",
        PRIORITY_RECURRING,
        30,
        ITEM_PENDING,
    ),
    (
        "R4-04",
        "Weekly closed-book pass on Weeks 1-3",
        "retrieve",
        PRIORITY_RECURRING,
        30,
        ITEM_PENDING,
    ),
]

# Retrieval generated from a Learn item — rule 6 places it after its source.
WEEK4_SOURCES = {"R4-01": "L4-01", "R4-02": "L4-02", "R4-03": "L4-03"}


def week4_items() -> list[SchedItem]:
    return [
        SchedItem(
            id=item_id,
            phase_index=2,
            week_index=4,
            guide_order=order,
            estimate_minutes=minutes,
            priority=priority,
            status=status,
            type=kind,
            source_item_id=WEEK4_SOURCES.get(item_id),
            title=title,
        )
        for order, (item_id, title, kind, priority, minutes, status) in enumerate(
            WEEK4_ROWS, start=1
        )
    ]


def filler(week: int, minutes: int, *, base_order: int, priority=PRIORITY_CORE) -> list[SchedItem]:
    """Baseline work for a week, in 30-minute units, all Core and pending."""
    blocks = minutes // 30
    return [
        SchedItem(
            id=f"W{week}-{n:02d}",
            phase_index=PHASE_OF_WEEK[week],
            week_index=week,
            guide_order=base_order + n,
            estimate_minutes=30,
            priority=priority,
            status=ITEM_PENDING,
            type="learn",
            title=f"Week {week} filler {n}",
        )
        for n in range(1, blocks + 1)
    ]


def canonical_items(week5: int = 660, week6: int = 480) -> list[SchedItem]:
    """Week 4 exactly as the handoff lists it, plus baseline work elsewhere."""
    items = week4_items()
    items += filler(5, week5, base_order=100)
    items += filler(6, week6, base_order=200)
    for week in (1, 2, 3):
        items += filler(week, 600, base_order=week * 10)
    for week in (7, 8, 9, 10, 11, 12):
        items += filler(week, 600, base_order=week * 300)
    return items


DEPENDENCIES = [SchedDependency("L4-02", "L4-03")]


def minutes_by_week(proposal) -> dict[int, int]:
    return {w.index: w.after_minutes for w in proposal.weeks}


# --- capacity ---------------------------------------------------------------


def test_effective_capacity_prefers_the_week_override_over_the_plan_default():
    week = SchedWeek(index=4, phase_index=2, override_capacity_minutes=420)
    assert effective_capacity_minutes(week, 720) == 420
    assert effective_capacity_minutes(SchedWeek(4, 2), 720) == 720


def test_a_proposed_override_beats_the_stored_one_and_none_clears_it():
    week = SchedWeek(index=4, phase_index=2, override_capacity_minutes=420)
    assert effective_capacity_minutes(week, 720, {4: 510}) == 510
    assert effective_capacity_minutes(week, 720, {4: None}) == 720
    # A week absent from the mapping keeps whatever it already had.
    assert effective_capacity_minutes(week, 720, {5: 900}) == 420


def test_plan_weeks_are_seven_day_blocks_from_the_start_date():
    plan = _plan()
    assert week_start_date(plan, 1) == START
    assert week_end_date(plan, 1) == date(2026, 8, 2)
    assert week_start_date(plan, 12) == date(2026, 10, 12)


def test_hours_are_display_only_and_never_round_a_half_hour_away():
    assert format_hours(720) == "12h"
    assert format_hours(690) == "11.5h"
    assert format_hours(660) == "11h"


# --- the baseline -----------------------------------------------------------


def test_the_canonical_week_4_baseline_is_660_of_720():
    """Sum = 90+90+60+120+90+30+60+30+30+30+30 = 660, as the handoff displays."""
    assert sum(i.estimate_minutes for i in week4_items()) == 660

    proposal = build_proposal(_plan(), _weeks(), canonical_items(), DEPENDENCIES)
    assert minutes_by_week(proposal)[4] == 660
    assert proposal.moves == ()
    assert proposal.unresolved == ()
    assert proposal.valid
    assert proposal.forecast_end_plan_week == 12
    assert not proposal.forecast_changed


def test_week_detail_totals_split_by_type_and_priority():
    workload = summarise_week(week4_items())
    assert workload.learn_minutes == 240
    assert workload.practice_minutes == 300
    assert workload.retrieval_minutes == 120
    assert workload.scheduled_plan_minutes == 660
    # The two facts under the Week detail title.
    assert (workload.core_complete, workload.core_total) == (3, 5)
    assert (workload.retrieval_complete, workload.retrieval_total) == (2, 4)


# --- the canonical override (V3.4 §3) ---------------------------------------


@pytest.fixture
def override_proposal():
    """Week 4 dropped to 420 minutes. The handoff's worked example."""
    return build_proposal(
        _plan(),
        _weeks(),
        canonical_items(),
        DEPENDENCIES,
        capacity_overrides={4: 420},
    )


def test_completed_work_is_preserved_in_its_current_week(override_proposal):
    """Rule 1. 90+90+120+30+30 = 360 minutes that cannot move."""
    week4 = override_proposal.placements
    for item_id in ("L4-01", "L4-02", "P4-01", "R4-01", "R4-02"):
        assert week4[item_id] == 4, item_id


def test_core_work_first_in_guide_order_takes_the_remaining_room(override_proposal):
    """420 - 360 completed = 60, and L4-03 is the first Core item that fits it."""
    assert override_proposal.placements["L4-03"] == 4
    assert minutes_by_week(override_proposal)[4] == 420


def test_the_240_minute_overflow_lands_as_60_in_week_5_and_180_in_week_6(
    override_proposal,
):
    moved = {m.item_id: m.to_week for m in override_proposal.moves}
    assert moved == {"P4-02": 6, "P4-03": 5, "P4-04": 6, "R4-03": 5, "R4-04": 6}

    to_five = sum(m.minutes for m in override_proposal.moves if m.to_week == 5)
    to_six = sum(m.minutes for m in override_proposal.moves if m.to_week == 6)
    assert (to_five, to_six) == (60, 180)
    assert to_five + to_six == 240
    assert override_proposal.displaced_minutes == 240
    assert override_proposal.unresolved == ()
    assert override_proposal.unresolved_minutes == 0


def test_the_recalculated_weekly_totals_match_the_handoff(override_proposal):
    totals = minutes_by_week(override_proposal)
    assert totals[4] == 420  # override, exactly full
    assert totals[5] == 720  # was 660, now exactly full
    assert totals[6] == 660  # was 480, 60 minutes of slack left
    for week in range(7, 13):
        assert totals[week] == 600, week  # nothing reached them


def test_the_forecast_does_not_move_when_slack_absorbs_the_overflow(override_proposal):
    assert override_proposal.forecast_end_plan_week == 12
    assert not override_proposal.forecast_changed
    assert override_proposal.valid
    assert override_proposal.reasons == ()


def test_a_carried_item_fills_slack_rather_than_evicting_a_resident(override_proposal):
    """Week 5's own 660 minutes of work all stay; only its 60 of slack is used.

    This is the difference between carrying work forward and re-packing the whole
    plan. A global re-pack would sort P4-02 ahead of Week 5's own Core items on
    guide order and displace one of them.
    """
    week5_residents = {f"W5-{n:02d}" for n in range(1, 23)}
    for item_id in week5_residents:
        assert override_proposal.placements[item_id] == 5


def test_retrieval_is_never_placed_before_the_item_it_retrieves(override_proposal):
    """Rule 6. R4-03 retrieves L4-03, which stayed in Week 4."""
    assert override_proposal.placements["L4-03"] == 4
    assert override_proposal.placements["R4-03"] >= 4


def test_hard_dependency_order_holds_through_the_replan(override_proposal):
    assert override_proposal.placements["L4-02"] <= override_proposal.placements["L4-03"]


# --- the unresolved variant (V3.4 §3, STATE 6) ------------------------------


def test_when_the_slack_runs_out_core_work_is_left_unplaced_and_apply_is_blocked():
    """Weeks 5 and 6 at 690/720 leave 60 minutes of slack between them.

    Deferring the four non-Core items frees 150 minutes, leaving Core P4-02's
    90 minutes against 60 minutes of slack — 30 minutes unresolved, exactly as
    the handoff reports. Core is never dropped to force a fit.
    """
    proposal = build_proposal(
        _plan(),
        _weeks(),
        canonical_items(week5=690, week6=690),
        DEPENDENCIES,
        capacity_overrides={4: 420},
        deferred_item_ids=["P4-03", "P4-04", "R4-03", "R4-04"],
    )

    assert [u.item_id for u in proposal.unresolved] == ["P4-02"]
    assert proposal.unresolved[0].reason == UNRESOLVED_NO_ROOM
    assert proposal.unresolved_minutes == 30
    assert not proposal.valid
    assert INVALID_UNRESOLVED in proposal.reasons
    assert INVALID_CORE_REMOVED in proposal.reasons


def test_raising_the_capacity_of_both_full_weeks_resolves_it():
    """The handoff's second valid option: 840 each gives 300 minutes of slack."""
    proposal = build_proposal(
        _plan(),
        _weeks(),
        canonical_items(week5=690, week6=690),
        DEPENDENCIES,
        capacity_overrides={4: 420, 5: 840, 6: 840},
    )
    assert proposal.unresolved == ()
    assert proposal.valid
    assert proposal.forecast_end_plan_week == 12


def test_adding_a_plan_week_resolves_it_and_moves_the_forecast_to_week_13():
    """The handoff's first valid option: 720 fresh minutes inside phase 2.

    The week is inserted after phase 2 rather than appended to the plan — an
    appended week belongs to phase 4, and phase-2 work may not move into it.
    """
    proposal = build_proposal(
        _plan(),
        _weeks(),
        canonical_items(week5=690, week6=690),
        DEPENDENCIES,
        capacity_overrides={4: 420},
        extra_weeks=1,
        insert_after_phase=2,
    )
    assert proposal.unresolved == ()
    assert proposal.valid
    assert proposal.forecast_end_plan_week == 13
    assert proposal.forecast_changed


def test_phase_order_is_a_ceiling_as_well_as_a_floor():
    """Phase-2 overflow may not colonise a phase-3 week even when one is empty."""
    items = [
        i for i in canonical_items(week5=720, week6=720) if not i.id.startswith(("W7", "W8", "W9"))
    ]
    proposal = build_proposal(_plan(), _weeks(), items, DEPENDENCIES, capacity_overrides={4: 420})
    # Weeks 7-9 are completely empty, and phase 2's work still does not go there.
    for move in proposal.moves:
        assert move.to_week <= 6, move
    assert proposal.unresolved != ()


# --- atomic items -----------------------------------------------------------


def test_an_item_larger_than_any_week_is_reported_as_not_fitting_not_split():
    plan = _plan(default_weekly_capacity_minutes=120, current_week_index=1)
    weeks = [SchedWeek(index=i, phase_index=1) for i in (1, 2)]
    items = [
        SchedItem("BIG", 1, 1, 1, estimate_minutes=180, priority=PRIORITY_CORE),
    ]
    proposal = build_proposal(plan, weeks, items)

    assert [u.reason for u in proposal.unresolved] == [UNRESOLVED_DOES_NOT_FIT]
    assert proposal.unresolved[0].minutes == 180
    assert not proposal.valid
    # It is one item in one place or nowhere — never two halves.
    assert "BIG" not in proposal.placements


def test_overflow_spreads_across_as_many_weeks_as_it_needs():
    plan = _plan(default_weekly_capacity_minutes=60, current_week_index=1)
    weeks = [SchedWeek(index=i, phase_index=1) for i in range(1, 6)]
    items = [
        SchedItem(f"I{n}", 1, 1, n, estimate_minutes=60, priority=PRIORITY_CORE)
        for n in range(1, 5)
    ]
    proposal = build_proposal(plan, weeks, items)

    assert [proposal.placements[f"I{n}"] for n in range(1, 5)] == [1, 2, 3, 4]
    assert proposal.valid
    assert proposal.forecast_end_plan_week == 4
    assert proposal.forecast_changed  # everything used to sit in week 1


def test_core_sorts_before_optional_even_when_optional_comes_first_in_the_guide():
    """Rule 5 outranks rule 4; rule 4 only breaks ties within a priority."""
    plan = _plan(default_weekly_capacity_minutes=60, current_week_index=1)
    weeks = [SchedWeek(index=i, phase_index=1) for i in (1, 2)]
    items = [
        SchedItem("OPT", 1, 1, 1, estimate_minutes=60, priority=PRIORITY_OPTIONAL),
        SchedItem("CORE", 1, 1, 2, estimate_minutes=60, priority=PRIORITY_CORE),
    ]
    proposal = build_proposal(plan, weeks, items)

    assert proposal.placements["CORE"] == 1
    assert proposal.placements["OPT"] == 2


# --- reopen -----------------------------------------------------------------


def test_reopening_an_item_that_still_fits_moves_nothing():
    """Reopen changes plan progress; it does not by itself move a date."""
    items = canonical_items()
    reopened = [
        SchedItem(**{**i.__dict__, "status": ITEM_PENDING}) if i.id == "L4-01" else i for i in items
    ]
    proposal = build_proposal(
        _plan(), _weeks(), reopened, DEPENDENCIES, capacity_overrides={4: 420}
    )

    # L4-01 is Core and first in guide order, so it keeps its 90 minutes; the
    # week's total is unchanged because completed work already consumed them.
    assert proposal.placements["L4-01"] == 4
    assert minutes_by_week(proposal)[4] == 420


def test_reopening_a_core_item_can_evict_optional_work_and_need_a_replan():
    plan = _plan(default_weekly_capacity_minutes=120, current_week_index=1)
    weeks = [SchedWeek(index=i, phase_index=1) for i in (1, 2)]
    items = [
        SchedItem(
            "CORE", 1, 1, 1, estimate_minutes=60, priority=PRIORITY_CORE, status=ITEM_PENDING
        ),
        SchedItem(
            "OPT", 1, 1, 2, estimate_minutes=60, priority=PRIORITY_OPTIONAL, status=ITEM_PENDING
        ),
    ]
    settled = build_proposal(plan, weeks, items)
    assert settled.moves == ()

    reopened = items + [
        SchedItem("DONE", 1, 1, 0, estimate_minutes=60, priority=PRIORITY_CORE, status=ITEM_PENDING)
    ]
    proposal = build_proposal(plan, weeks, reopened)
    assert proposal.placements["OPT"] == 2
    assert [m.item_id for m in proposal.moves] == ["OPT"]
    assert proposal.valid


# --- fixed deadlines --------------------------------------------------------


def test_a_fixed_plan_that_finishes_early_is_valid():
    plan = _plan(mode=MODE_FIXED, deadline=date(2026, 10, 18), current_week_index=1)
    proposal = build_proposal(plan, _weeks(), canonical_items(), DEPENDENCIES)
    # Week 12 ends 18 Oct 2026 — on the deadline, which is a ceiling, not a target.
    assert week_end_date(plan, 12) == date(2026, 10, 18)
    assert proposal.valid
    assert INVALID_DEADLINE not in proposal.reasons


def test_a_fixed_plan_that_lands_after_its_deadline_is_invalid():
    plan = _plan(mode=MODE_FIXED, deadline=date(2026, 10, 11), current_week_index=1)
    proposal = build_proposal(plan, _weeks(), canonical_items(), DEPENDENCIES)
    assert not proposal.valid
    assert INVALID_DEADLINE in proposal.reasons


def test_the_fixed_deadline_recovery_arithmetic_reconciles_to_1440_minutes():
    """V3.4 §5: 68h remaining, 44h available, a 24h gap closed exactly.

    Nine Optional items totalling 900 minutes plus eight recurring activities
    totalling 540 give 1440, leaving 2640 minutes of Core against 2640 available.
    No Core item is touched.
    """
    optional = [60, 60, 90, 120, 120, 180, 120, 90, 60]
    recurring = [30, 30, 30, 30, 90, 90, 120, 120]
    assert sum(optional) == 900
    assert sum(recurring) == 540
    assert sum(optional) + sum(recurring) == 1440

    remaining, available = 4080, 2640
    assert remaining - 1440 == available
    assert available == 4 * 660  # 4 plan weeks x 11h effective capacity

    plan = _plan(
        mode=MODE_FIXED,
        deadline=date(2026, 8, 23),
        current_week_index=1,
        default_weekly_capacity_minutes=660,
    )
    weeks = [SchedWeek(index=i, phase_index=1) for i in range(1, 5)]
    core = [
        SchedItem(f"C{n}", 1, 1, n, estimate_minutes=60, priority=PRIORITY_CORE)
        for n in range(1, 45)  # 44 x 60 = 2640
    ]
    extras = [
        SchedItem(f"O{n}", 1, 1, 100 + n, estimate_minutes=m, priority=PRIORITY_OPTIONAL)
        for n, m in enumerate(optional)
    ] + [
        SchedItem(f"R{n}", 1, 1, 200 + n, estimate_minutes=m, priority=PRIORITY_RECURRING)
        for n, m in enumerate(recurring)
    ]

    crowded = build_proposal(plan, weeks, core + extras)
    assert not crowded.valid

    relieved = build_proposal(
        plan,
        weeks,
        core + extras,
        deferred_item_ids=[i.id for i in extras],
    )
    assert relieved.unresolved == ()
    assert relieved.valid
    assert sum(w.after_minutes for w in relieved.weeks) == 2640


def test_raising_capacity_is_the_other_way_to_close_the_gap():
    """18h x 4 plan weeks = 4320 minutes, which covers all 4080."""
    assert 4 * 1080 >= 4080

    plan = _plan(
        mode=MODE_FIXED,
        deadline=date(2026, 8, 23),
        current_week_index=1,
        default_weekly_capacity_minutes=660,
    )
    weeks = [SchedWeek(index=i, phase_index=1) for i in range(1, 5)]
    items = [
        SchedItem(f"C{n}", 1, 1, n, estimate_minutes=60, priority=PRIORITY_CORE)
        for n in range(1, 69)  # 68 x 60 = 4080
    ]
    assert not build_proposal(plan, weeks, items).valid

    proposal = build_proposal(plan, weeks, items, default_capacity_minutes=1080)
    assert proposal.valid
    assert proposal.unresolved == ()


# --- validation -------------------------------------------------------------


def test_a_week_over_capacity_invalidates_the_proposal():
    """Completed work alone can exceed a lowered override, and that must not pass."""
    plan = _plan(default_weekly_capacity_minutes=60, current_week_index=1)
    weeks = [SchedWeek(index=1, phase_index=1)]
    items = [
        SchedItem("D1", 1, 1, 1, estimate_minutes=60, status=ITEM_COMPLETE),
        SchedItem("D2", 1, 1, 2, estimate_minutes=60, status=ITEM_COMPLETE),
    ]
    proposal = build_proposal(plan, weeks, items)
    assert not proposal.valid
    assert INVALID_OVER_CAPACITY in proposal.reasons


def test_core_work_never_disappears_without_explicit_confirmation():
    plan = _plan(default_weekly_capacity_minutes=60, current_week_index=1)
    weeks = [SchedWeek(index=1, phase_index=1)]
    items = [
        SchedItem("KEEP", 1, 1, 1, estimate_minutes=60, priority=PRIORITY_CORE),
        SchedItem("DROP", 1, 1, 2, estimate_minutes=60, priority=PRIORITY_CORE),
    ]

    unconfirmed = build_proposal(plan, weeks, items, deferred_item_ids=["DROP"])
    assert not unconfirmed.valid
    assert INVALID_CORE_REMOVED in unconfirmed.reasons

    confirmed = build_proposal(
        plan, weeks, items, deferred_item_ids=["DROP"], confirmed_core_removals=["DROP"]
    )
    assert confirmed.valid
    assert "DROP" not in confirmed.placements


def test_deferring_optional_work_needs_no_confirmation():
    plan = _plan(default_weekly_capacity_minutes=60, current_week_index=1)
    weeks = [SchedWeek(index=1, phase_index=1)]
    items = [
        SchedItem("KEEP", 1, 1, 1, estimate_minutes=60, priority=PRIORITY_CORE),
        SchedItem("SKIP", 1, 1, 2, estimate_minutes=60, priority=PRIORITY_OPTIONAL),
    ]
    proposal = build_proposal(plan, weeks, items, deferred_item_ids=["SKIP"])
    assert proposal.valid
    assert proposal.deferred_item_ids == ("SKIP",)


def test_reason_codes_are_stable_and_deduplicated():
    plan = _plan(
        mode=MODE_FIXED,
        deadline=date(2026, 7, 28),
        default_weekly_capacity_minutes=30,
        current_week_index=1,
    )
    weeks = [SchedWeek(index=1, phase_index=1)]
    items = [
        SchedItem("A", 1, 1, 1, estimate_minutes=30, priority=PRIORITY_CORE),
        SchedItem("B", 1, 1, 2, estimate_minutes=30, priority=PRIORITY_CORE),
    ]
    proposal = build_proposal(plan, weeks, items)
    # Declaration order, not discovery order, and one line per distinct problem.
    assert proposal.reasons == (
        INVALID_DEADLINE,
        INVALID_CORE_REMOVED,
        INVALID_UNRESOLVED,
    )
    assert len(proposal.reasons) == len(set(proposal.reasons))


def test_the_proposal_records_the_revision_it_was_computed_against():
    proposal = build_proposal(_plan(revision=7), _weeks(), canonical_items(), DEPENDENCIES)
    assert proposal.base_plan_revision == 7


def test_the_scheduler_never_mutates_its_inputs():
    plan, weeks, items = _plan(), _weeks(), canonical_items()
    snapshot = [(i.id, i.week_index, i.status) for i in items]
    build_proposal(plan, weeks, items, DEPENDENCIES, capacity_overrides={4: 420})
    assert [(i.id, i.week_index, i.status) for i in items] == snapshot
