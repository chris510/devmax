"""The deterministic weekly scheduler. See docs/STUDY-PLAN-SPEC.md §Scheduler.

Pure functions, independent of FastAPI request context and of the ORM, so they're
directly unit-testable. Nothing here writes — `build_proposal` returns a proposal
and the service layer decides whether to persist it, which is what makes
"never mutate the saved schedule until the user confirms" structural rather than
a habit.

All arithmetic is in **integer minutes**. Hours exist only in display strings.
Global SM-2 review time is never part of a plan's capacity: the review queue is
not owned by the active plan, so an anatomy plan can't be made to reflow by how
many cards happen to be due.
"""

from collections.abc import Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import date, timedelta

DAYS_PER_WEEK = 7

MODE_FLEXIBLE = "flexible"
MODE_FIXED = "fixed"

ITEM_PENDING = "pending"
ITEM_COMPLETE = "complete"
ITEM_DEFERRED = "deferred"
ITEM_REMOVED = "removed"

PRIORITY_CORE = "core"
PRIORITY_OPTIONAL = "optional"
PRIORITY_RECURRING = "recurring"

DEP_HARD = "hard"

# Why a carried item could not be placed.
UNRESOLVED_NO_ROOM = "no_room"
UNRESOLVED_DOES_NOT_FIT = "does_not_fit"
UNRESOLVED_DEPENDENCY_CYCLE = "dependency_cycle"

# Validation failure reasons, returned as stable codes so the client renders its
# own copy rather than parsing prose.
INVALID_OVER_CAPACITY = "week_over_capacity"
INVALID_DEPENDENCY_ORDER = "dependency_out_of_order"
INVALID_DEADLINE = "deadline_missed"
INVALID_CORE_REMOVED = "core_removed_without_confirmation"
INVALID_UNRESOLVED = "work_unplaced"


@dataclass(frozen=True)
class SchedPlan:
    mode: str
    start_date: date
    default_weekly_capacity_minutes: int
    current_week_index: int = 1
    deadline: date | None = None
    revision: int = 1


@dataclass(frozen=True)
class SchedWeek:
    index: int
    phase_index: int
    override_capacity_minutes: int | None = None


@dataclass(frozen=True)
class SchedItem:
    id: str
    phase_index: int
    week_index: int
    guide_order: int
    estimate_minutes: int
    priority: str = PRIORITY_CORE
    status: str = ITEM_PENDING
    type: str = "learn"
    source_item_id: str | None = None
    title: str = ""


@dataclass(frozen=True)
class SchedDependency:
    prerequisite_item_id: str
    dependent_item_id: str
    kind: str = DEP_HARD


@dataclass(frozen=True)
class WeekPlacement:
    index: int
    capacity_minutes: int
    before_minutes: int
    after_minutes: int
    item_ids: tuple[str, ...]

    @property
    def slack_minutes(self) -> int:
        return self.capacity_minutes - self.after_minutes

    @property
    def changed(self) -> bool:
        return self.before_minutes != self.after_minutes

    @property
    def over_capacity(self) -> bool:
        return self.after_minutes > self.capacity_minutes


@dataclass(frozen=True)
class ItemMove:
    item_id: str
    from_week: int
    to_week: int
    minutes: int


@dataclass(frozen=True)
class Unresolved:
    item_id: str
    minutes: int
    reason: str


@dataclass(frozen=True)
class Proposal:
    """What a confirmed Apply would do. Never persisted by this module."""

    base_plan_revision: int
    weeks: tuple[WeekPlacement, ...]
    placements: Mapping[str, int]
    moves: tuple[ItemMove, ...]
    unresolved: tuple[Unresolved, ...]
    # Aggregate shortfall, not the sum of unplaced estimates: the displayed figure
    # is "how many minutes have nowhere to go", which is what remains after every
    # eligible week's slack is used. Both numbers are shown on decision screens.
    unresolved_minutes: int
    forecast_end_plan_week: int
    forecast_changed: bool
    valid: bool
    reasons: tuple[str, ...] = ()
    deferred_item_ids: tuple[str, ...] = ()

    @property
    def displaced_minutes(self) -> int:
        return sum(m.minutes for m in self.moves)

    @property
    def affected_weeks(self) -> tuple[WeekPlacement, ...]:
        return tuple(w for w in self.weeks if w.changed)


# --- capacity ---------------------------------------------------------------


def effective_capacity_minutes(
    week: SchedWeek,
    default_minutes: int,
    overrides: Mapping[int, int | None] | None = None,
) -> int:
    """`week.override ?? plan.default`, with a proposed override taking precedence.

    `overrides` models an unconfirmed capacity edit: a value replaces the week's
    stored override, and an explicit ``None`` clears it back to the plan default.
    A week absent from the mapping keeps whatever it already had.
    """
    if overrides is not None and week.index in overrides:
        proposed = overrides[week.index]
        return default_minutes if proposed is None else proposed
    if week.override_capacity_minutes is not None:
        return week.override_capacity_minutes
    return default_minutes


def week_start(start_date: date, index: int) -> date:
    """Plan weeks are seven-day blocks from `start_date`. 1-based.

    This is the only place a plan week becomes a calendar date, and it is
    deliberately coarse: everything downstream renders `week of <date>`. No
    completion *day* is ever derived from weekly capacity.

    Takes a bare date rather than a plan so the preview screen — which has a
    draft, not a plan — can use it too instead of reimplementing the arithmetic.
    """
    return start_date + timedelta(days=DAYS_PER_WEEK * (index - 1))


def week_of_label(start_date: date, index: int) -> str:
    """`week of 19 Oct`. Plan-week precision — never a completion day."""
    start = week_start(start_date, index)
    return f"week of {start.day} {start.strftime('%b')}"


def forecast_label(start_date: date, index: int) -> str:
    return f"Est. completion · {week_of_label(start_date, index)}"


def week_start_date(plan: SchedPlan, index: int) -> date:
    return week_start(plan.start_date, index)


def week_end_date(plan: SchedPlan, index: int) -> date:
    return week_start_date(plan, index) + timedelta(days=DAYS_PER_WEEK - 1)


# --- ordering ---------------------------------------------------------------


def _core_rank(priority: str) -> int:
    """Rule 5 is exactly "Core before Optional" and says nothing about Recurring.

    So Core sorts first and everything else ties, letting rule 4 (original guide
    order within a phase) break it. Inventing an Optional-before-Recurring rule
    would reorder guides that interleave them, which no rule asks for.
    """
    return 0 if priority == PRIORITY_CORE else 1


def _sort_key(item: SchedItem) -> tuple[int, int, int, str]:
    # Rule 3 (phase order) → rule 5 (Core first) → rule 4 (guide order). The id is
    # a final tiebreak so the result is stable for identical guide orders.
    return (item.phase_index, _core_rank(item.priority), item.guide_order, item.id)


def _with_extra_weeks(
    weeks: Sequence[SchedWeek], extra: int, insert_after_phase: int | None
) -> tuple[list[SchedWeek], int | None]:
    """Model the "add one plan week" resolution.

    Returns the new week list and the index *after* which later weeks shifted, so
    the caller can move the items that live in them. Returning the boundary rather
    than shifting items here keeps this function operating on weeks alone.

    A fresh week is **inserted into the phase that needs it**, not appended to the
    end of the plan — appending would put phase-2 overflow in a phase-4 week,
    which rule 3 forbids and which is why the option would never validate. Later
    weeks shift up by one, which is exactly what moves the forecast from Week 12
    to Week 13.
    """
    if extra <= 0 or not weeks:
        return list(weeks), None
    if insert_after_phase is None:
        last = weeks[-1]
        appended = list(weeks) + [
            SchedWeek(index=last.index + n, phase_index=last.phase_index)
            for n in range(1, extra + 1)
        ]
        return appended, None

    boundary = max(
        (w.index for w in weeks if w.phase_index == insert_after_phase), default=None
    )
    if boundary is None:
        return list(weeks), None

    result = [w for w in weeks if w.index <= boundary]
    result += [
        SchedWeek(index=boundary + n, phase_index=insert_after_phase)
        for n in range(1, extra + 1)
    ]
    result += [
        SchedWeek(
            index=w.index + extra,
            phase_index=w.phase_index,
            override_capacity_minutes=w.override_capacity_minutes,
        )
        for w in weeks
        if w.index > boundary
    ]
    return result, boundary


def _hard_prerequisites(
    dependencies: Iterable[SchedDependency],
) -> dict[str, set[str]]:
    prereqs: dict[str, set[str]] = {}
    for dep in dependencies:
        if dep.kind != DEP_HARD:
            continue
        prereqs.setdefault(dep.dependent_item_id, set()).add(dep.prerequisite_item_id)
    return prereqs


# --- the scheduler ----------------------------------------------------------


def build_proposal(
    plan: SchedPlan,
    weeks: Sequence[SchedWeek],
    items: Sequence[SchedItem],
    dependencies: Sequence[SchedDependency] = (),
    *,
    capacity_overrides: Mapping[int, int | None] | None = None,
    default_capacity_minutes: int | None = None,
    deferred_item_ids: Collection[str] = (),
    extra_weeks: int = 0,
    insert_after_phase: int | None = None,
    confirmed_core_removals: Collection[str] = (),
) -> Proposal:
    """Apply the ten allocation rules and return what a confirmed Apply would do.

    The schedule is **stable by default**: an item stays where it is unless its
    week can no longer hold it. That is the difference between rule 7 ("carry work
    forward when the current week is full") and a global re-pack, and it is what
    keeps a resident item from being displaced by work carried in from an earlier
    week — carried work fills slack, it never evicts.

    Two passes:

      A. Stabilise. Walk weeks in ascending order. Completed work is pinned
         (rule 1). Open residents are kept in Core-then-guide-order while they
         fit; the rest are evicted into a carry pool.
      B. Place. Each carried item goes to the earliest week at or after its floor
         with room for it. The floor is the latest of the current plan week, the
         item's phase's first week, its hard prerequisites' weeks, its source
         item's week (rule 6), and its own original week — so carry is always
         forward and never crosses an unmet dependency (rules 2, 3, 8).

    Rule 3 is a **ceiling as well as a floor**: work never drifts past the last
    week of its own phase. Without that, phase-2 overflow would quietly colonise
    phase 4's weeks and the phase structure would mean nothing — and the plan
    would never report that it has run out of room, because there is always a
    later week somewhere.

    Nothing is dropped automatically: an item with nowhere to go comes back as
    `unresolved`, and a Core item among them makes the proposal invalid (rule 9).
    """
    default_minutes = (
        plan.default_weekly_capacity_minutes
        if default_capacity_minutes is None
        else default_capacity_minutes
    )
    deferred = set(deferred_item_ids)

    # Captured before any week insertion renumbers things, so `forecast_changed`
    # answers "did the plan get longer" rather than comparing shifted to shifted.
    previous_forecast = max(
        (
            i.week_index
            for i in items
            if i.status not in (ITEM_REMOVED, ITEM_DEFERRED) and i.id not in deferred
        ),
        default=plan.current_week_index,
    )

    week_list, shift_boundary = _with_extra_weeks(
        sorted(weeks, key=lambda w: w.index), extra_weeks, insert_after_phase
    )
    if shift_boundary is not None:
        # Inserting a week pushes every later week up, so the work living in them
        # moves with them. Doing this before pass A means those items are still
        # residents of their own (renumbered) week rather than looking orphaned.
        items = [
            replace(i, week_index=i.week_index + extra_weeks)
            if i.week_index > shift_boundary
            else i
            for i in items
        ]

    capacity = {
        w.index: effective_capacity_minutes(w, default_minutes, capacity_overrides)
        for w in week_list
    }
    week_order = [w.index for w in week_list]
    # Rule 3's bounds. A phase owns a contiguous span of weeks; its work stays
    # inside that span in both directions.
    phase_floor: dict[int, int] = {}
    phase_ceiling: dict[int, int] = {}
    for w in week_list:
        if w.phase_index in phase_floor:
            phase_floor[w.phase_index] = min(phase_floor[w.phase_index], w.index)
            phase_ceiling[w.phase_index] = max(phase_ceiling[w.phase_index], w.index)
        else:
            phase_floor[w.phase_index] = w.index
            phase_ceiling[w.phase_index] = w.index

    scheduled = [
        i for i in items if i.status != ITEM_REMOVED and i.id not in deferred
        and i.status != ITEM_DEFERRED
    ]
    by_id = {i.id: i for i in scheduled}
    before_minutes = {idx: 0 for idx in week_order}
    for item in scheduled:
        if item.week_index in before_minutes:
            before_minutes[item.week_index] += item.estimate_minutes

    # --- pass A: stabilise ---------------------------------------------------
    placements: dict[str, int] = {}
    load = {idx: 0 for idx in week_order}
    carried: list[SchedItem] = []

    residents: dict[int, list[SchedItem]] = {idx: [] for idx in week_order}
    orphans: list[SchedItem] = []
    for item in scheduled:
        if item.week_index in residents:
            residents[item.week_index].append(item)
        else:
            # The item's week no longer exists (a duplicated or trimmed plan).
            orphans.append(item)

    for idx in week_order:
        room = capacity[idx]
        pinned = [i for i in residents[idx] if i.status == ITEM_COMPLETE]
        for item in pinned:
            placements[item.id] = idx
            load[idx] += item.estimate_minutes
            room -= item.estimate_minutes

        for item in sorted(
            (i for i in residents[idx] if i.status != ITEM_COMPLETE), key=_sort_key
        ):
            if item.estimate_minutes <= room:
                placements[item.id] = idx
                load[idx] += item.estimate_minutes
                room -= item.estimate_minutes
            else:
                carried.append(item)

    carried.extend(sorted(orphans, key=_sort_key))

    # --- pass B: place -------------------------------------------------------
    prereqs = _hard_prerequisites(dependencies)
    unresolved: list[Unresolved] = []

    def ceiling_for(item: SchedItem) -> int:
        return phase_ceiling.get(item.phase_index, week_order[-1] if week_order else 1)

    def floor_for(item: SchedItem) -> int | None:
        """Earliest week this item may occupy, or None if a prerequisite is unplaced."""
        floor = max(plan.current_week_index, phase_floor.get(item.phase_index, 1))
        # Carry is forward: an evicted item never lands in a week before the one
        # it came from, even if that earlier week happens to have slack.
        floor = max(floor, item.week_index)
        for prereq_id in prereqs.get(item.id, ()):  # rules 2 and 8
            if prereq_id not in by_id:
                continue  # removed or deferred — no ordering left to preserve
            placed = placements.get(prereq_id)
            if placed is None:
                return None
            floor = max(floor, placed)
        # Rule 6: plan-local retrieval goes after the item it retrieves. Same week
        # is "after" — R4-01 sits alongside L4-01 in the canonical fixture.
        if item.source_item_id and item.source_item_id in by_id:
            placed = placements.get(item.source_item_id)
            if placed is None:
                return None
            floor = max(floor, placed)
        return floor

    pending = list(carried)
    while pending:
        progressed = False
        deferred_pass: list[SchedItem] = []
        for item in pending:
            floor = floor_for(item)
            if floor is None:
                deferred_pass.append(item)
                continue
            progressed = True
            ceiling = ceiling_for(item)
            eligible = [idx for idx in week_order if floor <= idx <= ceiling]
            target = next(
                (
                    idx
                    for idx in eligible
                    if capacity[idx] - load[idx] >= item.estimate_minutes
                ),
                None,
            )
            if target is None:
                # "Doesn't fit in a single study week" is a different problem from
                # "the weeks are full", and the app offers different resolutions.
                roomiest = max((capacity[idx] for idx in eligible), default=0)
                reason = (
                    UNRESOLVED_DOES_NOT_FIT
                    if item.estimate_minutes > roomiest
                    else UNRESOLVED_NO_ROOM
                )
                unresolved.append(Unresolved(item.id, item.estimate_minutes, reason))
                continue
            placements[item.id] = target
            load[target] += item.estimate_minutes
        if not progressed:
            # Every remaining item is waiting on a prerequisite that will never be
            # placed — a cycle, or a dependency on something unresolved above.
            unresolved.extend(
                Unresolved(i.id, i.estimate_minutes, UNRESOLVED_DEPENDENCY_CYCLE)
                for i in sorted(deferred_pass, key=_sort_key)
            )
            break
        pending = deferred_pass

    # --- assemble ------------------------------------------------------------
    week_items: dict[int, list[str]] = {idx: [] for idx in week_order}
    for item_id, idx in placements.items():
        week_items[idx].append(item_id)
    for idx in week_order:
        week_items[idx].sort(key=lambda i: _sort_key(by_id[i]))

    placements_out = [
        WeekPlacement(
            index=idx,
            capacity_minutes=capacity[idx],
            before_minutes=before_minutes.get(idx, 0),
            after_minutes=load[idx],
            item_ids=tuple(week_items[idx]),
        )
        for idx in week_order
    ]

    moves = tuple(
        ItemMove(
            item_id=item.id,
            from_week=item.week_index,
            to_week=placements[item.id],
            minutes=item.estimate_minutes,
        )
        for item in sorted(scheduled, key=_sort_key)
        if item.id in placements and placements[item.id] != item.week_index
    )

    # Aggregate shortfall: what is left after every eligible week's slack is spent.
    unplaced_minutes = sum(u.minutes for u in unresolved)
    if unresolved:
        eligible_union: set[int] = set()
        for u in unresolved:
            item = by_id[u.item_id]
            low = floor_for(item) or plan.current_week_index
            high = ceiling_for(item)
            eligible_union.update(idx for idx in week_order if low <= idx <= high)
        available = sum(max(0, capacity[idx] - load[idx]) for idx in eligible_union)
        unresolved_minutes = max(0, unplaced_minutes - available)
    else:
        unresolved_minutes = 0

    occupied = [idx for idx in week_order if week_items[idx]]
    forecast = max(occupied) if occupied else plan.current_week_index

    valid, reasons = _validate(
        plan=plan,
        weeks=placements_out,
        placements=placements,
        by_id=by_id,
        dependencies=dependencies,
        unresolved=tuple(unresolved),
        forecast=forecast,
        deferred=deferred,
        all_items=items,
        confirmed_core_removals=set(confirmed_core_removals),
    )

    return Proposal(
        base_plan_revision=plan.revision,
        weeks=tuple(placements_out),
        placements=placements,
        moves=moves,
        unresolved=tuple(unresolved),
        unresolved_minutes=unresolved_minutes,
        forecast_end_plan_week=forecast,
        forecast_changed=forecast != previous_forecast,
        valid=valid,
        reasons=reasons,
        deferred_item_ids=tuple(sorted(deferred)),
    )


def _validate(
    *,
    plan: SchedPlan,
    weeks: Sequence[WeekPlacement],
    placements: Mapping[str, int],
    by_id: Mapping[str, SchedItem],
    dependencies: Sequence[SchedDependency],
    unresolved: Sequence[Unresolved],
    forecast: int,
    deferred: Collection[str],
    all_items: Sequence[SchedItem],
    confirmed_core_removals: Collection[str],
) -> tuple[bool, tuple[str, ...]]:
    """V3.4 §2's validation predicate, returning stable reason codes.

    A proposal is valid only when every affected week fits, every hard dependency
    stays ordered, a fixed plan still lands on or before its deadline, no Core
    work vanished without explicit confirmation, and nothing is left unplaced.
    """
    reasons: list[str] = []

    if any(w.over_capacity for w in weeks):
        reasons.append(INVALID_OVER_CAPACITY)

    for dep in dependencies:
        if dep.kind != DEP_HARD:
            continue
        prereq = placements.get(dep.prerequisite_item_id)
        dependent = placements.get(dep.dependent_item_id)
        if prereq is None or dependent is None:
            continue
        if prereq > dependent:
            reasons.append(INVALID_DEPENDENCY_ORDER)
            break

    if plan.mode == MODE_FIXED and plan.deadline is not None:
        # Finishing early is valid; the deadline is a ceiling.
        if week_end_date(plan, forecast) > plan.deadline:
            reasons.append(INVALID_DEADLINE)

    dropped_core = {
        item.id
        for item in all_items
        if item.priority == PRIORITY_CORE
        and item.status not in (ITEM_REMOVED,)
        and (item.id in deferred or item.id not in placements)
        and item.id not in confirmed_core_removals
    }
    if dropped_core:
        reasons.append(INVALID_CORE_REMOVED)

    if unresolved:
        reasons.append(INVALID_UNRESOLVED)

    # Stable order, no duplicates — the client renders one line per code.
    ordered = tuple(
        code
        for code in (
            INVALID_OVER_CAPACITY,
            INVALID_DEPENDENCY_ORDER,
            INVALID_DEADLINE,
            INVALID_CORE_REMOVED,
            INVALID_UNRESOLVED,
        )
        if code in reasons
    )
    return (not ordered), ordered


# --- reporting helpers ------------------------------------------------------


@dataclass(frozen=True)
class WeekWorkload:
    learn_minutes: int = 0
    practice_minutes: int = 0
    retrieval_minutes: int = 0
    core_minutes: int = 0
    optional_minutes: int = 0
    recurring_minutes: int = 0
    core_total: int = 0
    core_complete: int = 0
    retrieval_total: int = 0
    retrieval_complete: int = 0
    item_ids: tuple[str, ...] = field(default_factory=tuple)

    @property
    def scheduled_plan_minutes(self) -> int:
        """learn + practice + plan-local retrieval. Global reviews are not here."""
        return self.learn_minutes + self.practice_minutes + self.retrieval_minutes


def summarise_week(items: Iterable[SchedItem]) -> WeekWorkload:
    """Per-week totals for Week detail. Counts only scheduled work."""
    learn = practice = retrieval = 0
    core = optional = recurring = 0
    core_total = core_done = retr_total = retr_done = 0
    ids: list[str] = []
    for item in items:
        if item.status in (ITEM_REMOVED, ITEM_DEFERRED):
            continue
        ids.append(item.id)
        minutes = item.estimate_minutes
        if item.type == "learn":
            learn += minutes
        elif item.type == "practice":
            practice += minutes
        else:
            retrieval += minutes
            retr_total += 1
            if item.status == ITEM_COMPLETE:
                retr_done += 1
        if item.priority == PRIORITY_CORE:
            core += minutes
            core_total += 1
            if item.status == ITEM_COMPLETE:
                core_done += 1
        elif item.priority == PRIORITY_OPTIONAL:
            optional += minutes
        else:
            recurring += minutes
    return WeekWorkload(
        learn_minutes=learn,
        practice_minutes=practice,
        retrieval_minutes=retrieval,
        core_minutes=core,
        optional_minutes=optional,
        recurring_minutes=recurring,
        core_total=core_total,
        core_complete=core_done,
        retrieval_total=retr_total,
        retrieval_complete=retr_done,
        item_ids=tuple(ids),
    )


def format_hours(minutes: int) -> str:
    """Display only. `720 -> "12h"`, `690 -> "11.5h"`. Never used in arithmetic."""
    hours = minutes / 60
    return f"{hours:.0f}h" if hours == int(hours) else f"{hours:.1f}h"
