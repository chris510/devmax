"""Validation between the importer and the database.

The model produces structure; this module decides whether any of it is allowed
to become a plan. Two rules govern everything here:

1. **Never trust model arithmetic.** Every total, every weekly load, every
   capacity comparison is recomputed from the item estimates in application code.
   The model is told explicitly not to make the numbers fit, precisely so that a
   plan which does not fit surfaces as a finding rather than as quietly shrunken
   estimates.
2. **Model output never creates an active plan.** `validate_import` produces a
   *preview* plus a list of checks. Creation is a separate, confirmed request,
   and it reads the preview — never the raw response.

Pure functions over plain dicts, so the whole gate is unit-testable without a
database or a network.
"""

import re
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any

from app.models import (
    CONFIDENCE_NEEDS_REVIEW,
    DEP_IMPORTED,
    ESTIMATE_IMPORTED,
    ITEM_RETRIEVE,
    MODE_FIXED,
    ORIGIN_GENERATED,
    PRIORITY_CORE,
    StudyPlan,
    StudyPlanItem,
    StudyPlanItemDependency,
    StudyPlanPhase,
    StudyPlanWeek,
)
from app.services import study_plan_scheduler as sched
from app.services.study_plan import subject_supports_cards

# A guide shorter than this cannot produce a plan worth reviewing, and one longer
# than this is almost certainly a whole book. Both are user errors with a clear
# message rather than a paid call that fails validation.
MIN_GUIDE_CHARS = 200
MAX_GUIDE_CHARS = 400_000

# The product's stated range. Outside it is allowed but reported, because a
# 3-week or 30-week plan is a different thing from what the screens were designed
# around and the user should know they have asked for one.
TYPICAL_MIN_WEEKS = 8
TYPICAL_MAX_WEEKS = 12
HARD_MIN_WEEKS = 2
HARD_MAX_WEEKS = 52

ESTIMATE_INCREMENT = 30

OVERVIEW_MAX_WORDS = 5
# V3.5 §4 rule 2 says "2-5 words", but its own worked examples are single words —
# "Databases", "Coordination", "Filtration", "Acid-base". A one-word label that
# names the subject is exactly what the rule is after, so the enforced floor is
# "not empty" and the real constraint is rule 7: no vague fragments.
OVERVIEW_MAX_CHARS = 28
# Fragments that identify nothing. "Systems" is banned and "Databases" is not,
# which is the whole distinction: one names the subject, the other gestures at it.
BANNED_OVERVIEW_TITLES = frozenset(
    {
        "systems",
        "advanced",
        "part 2",
        "part two",
        "misc",
        "other",
        "general",
        "review",
        "more",
        "extras",
        "continued",
        "wrap up",
    }
)
ELLIPSES = ("...", "…")

CHECK_OK = "ok"
CHECK_NEEDS_REVIEW = "needs_review"
CHECK_BLOCKED = "blocked"


class ImportError_(ValueError):
    """The response cannot be turned into a preview at all.

    Distinct from a failed check: a check is something the user resolves on the
    Review plan screen, while this means the structure is unusable and the only
    move is to retry the import.
    """


@dataclass
class Check:
    """One row on the Review plan screen.

    `destination` is what gives a row its disclosure arrow. Resolved rows are a
    single status line with nowhere to go — only exceptions are tappable.
    """

    key: str
    label: str
    status: str
    value: str
    detail: str = ""
    destination: str | None = None
    item_keys: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "label": self.label,
            "status": self.status,
            "value": self.value,
            "detail": self.detail,
            "destination": self.destination,
            "item_keys": list(self.item_keys),
        }


@dataclass
class ImportResult:
    preview: dict[str, Any]
    checks: list[Check]

    @property
    def can_create(self) -> bool:
        """Create Plan unlocks only when every check is resolved."""
        return all(c.status == CHECK_OK for c in self.checks)

    def as_dict(self) -> dict[str, Any]:
        return {
            "preview": self.preview,
            "checks": [c.as_dict() for c in self.checks],
            "can_create": self.can_create,
        }


# --- request-side validation ------------------------------------------------


def validate_request(
    *,
    guide_text: str,
    requested_weeks: int,
    weekly_capacity_minutes: int,
    mode: str,
    deadline: date | None,
    start_date: date,
) -> list[str]:
    """Reject what should never reach a paid call. Returns error strings."""
    errors: list[str] = []
    stripped = guide_text.strip()
    if len(stripped) < MIN_GUIDE_CHARS:
        errors.append(
            f"the guide is {len(stripped)} characters; at least "
            f"{MIN_GUIDE_CHARS} are needed to build a plan"
        )
    if len(stripped) > MAX_GUIDE_CHARS:
        errors.append(f"the guide is longer than {MAX_GUIDE_CHARS} characters")
    if not HARD_MIN_WEEKS <= requested_weeks <= HARD_MAX_WEEKS:
        errors.append(f"duration must be between {HARD_MIN_WEEKS} and {HARD_MAX_WEEKS} weeks")
    if weekly_capacity_minutes <= 0 or weekly_capacity_minutes % ESTIMATE_INCREMENT:
        errors.append("weekly capacity must be a positive multiple of 30 minutes")
    if mode == MODE_FIXED and deadline is None:
        errors.append("a fixed plan needs a deadline")
    if mode != MODE_FIXED and deadline is not None:
        errors.append("a flexible plan cannot carry a deadline")
    if deadline is not None and deadline <= start_date:
        errors.append("the deadline is on or before the start date")
    return errors


# --- overview titles --------------------------------------------------------


def overview_title_problem(title: str) -> str | None:
    """Why this concise title is unusable, or None. See V3.5 §4."""
    text = title.strip()
    if not text:
        return "is empty"
    if any(e in text for e in ELLIPSES):
        return "is a truncation rather than a label"
    words = text.split()
    if len(words) > OVERVIEW_MAX_WORDS:
        return f"is {len(words)} words; concise titles are at most {OVERVIEW_MAX_WORDS}"
    if len(text) > OVERVIEW_MAX_CHARS:
        return f"is {len(text)} characters; concise titles fit in {OVERVIEW_MAX_CHARS}"
    if text.casefold() in BANNED_OVERVIEW_TITLES:
        return "is too vague to identify the week"
    return None


_COLLAPSE = re.compile(r"\s+")


def _same_text(left: str, right: str) -> bool:
    a = _COLLAPSE.sub(" ", unicodedata.normalize("NFKC", left)).strip()
    b = _COLLAPSE.sub(" ", unicodedata.normalize("NFKC", right)).strip()
    return a == b


def _locate(guide_text: str, excerpt: str) -> tuple[int, int] | None:
    """Find `excerpt` in the guide, tolerating whitespace differences."""
    found = guide_text.find(excerpt)
    if found >= 0:
        return found, found + len(excerpt)
    words = [re.escape(w) for w in excerpt.split()]
    if not words:
        return None
    match = re.search(r"\s+".join(words), guide_text)
    return (match.start(), match.end()) if match else None


def _normalise_offsets(
    guide_text: str, start: Any, end: Any, excerpt: str
) -> tuple[int | None, int | None, bool]:
    """Resolve a source span against the verbatim guide. Returns (start, end, ok).

    Offsets are **recomputed, not trusted** — the same rule the rest of this
    module applies to arithmetic. Counting characters over a ten-thousand-character
    document is the one part of the task a language model is genuinely bad at: in a
    live run against `docs/CURRICULUM.md`, 31 of 72 items came back with offsets
    that pointed at the wrong span, while the excerpts themselves were verbatim.

    So the excerpt is the source of truth. If the model's offsets happen to frame
    it, they are kept; otherwise the excerpt is located in the guide and the real
    offsets are substituted. Only an excerpt that appears nowhere in the guide is
    a genuine provenance failure — that one means the item was invented, which is
    worth blocking on.

    A missing span with no excerpt is honest and allowed; the rubric asks for null
    rather than a guess.
    """
    if not excerpt.strip():
        # Nothing to locate. Any offsets sent alongside an empty excerpt are
        # unverifiable, so they are dropped rather than stored unchecked.
        return None, None, True

    if start is not None and end is not None:
        try:
            start_i, end_i = int(start), int(end)
        except (TypeError, ValueError):
            start_i = end_i = -1
        if 0 <= start_i < end_i <= len(guide_text) and _same_text(
            guide_text[start_i:end_i], excerpt
        ):
            return start_i, end_i, True

    located = _locate(guide_text, excerpt)
    if located is None:
        return None, None, False
    return located[0], located[1], True


# --- the gate ---------------------------------------------------------------


def validate_import(
    raw: Mapping[str, Any],
    *,
    guide_text: str,
    requested_weeks: int,
    weekly_capacity_minutes: int,
    mode: str,
    deadline: date | None,
    start_date: date,
    resolutions: Mapping[str, Any] | None = None,
) -> ImportResult:
    """Recompute everything the model claimed, and report what needs a decision.

    `resolutions` carries the user's answers from a previous pass — reviewed
    estimates, acknowledged omissions, approved generated retrieval, confirmed
    dependencies — so re-validating after an edit closes the checks it resolved
    without discarding the rest of the preview.
    """
    resolutions = dict(resolutions or {})
    checks: list[Check] = []

    phases = _read_phases(raw)
    weeks = _read_weeks(raw, phases)
    items = _read_items(raw, weeks, guide_text)
    dependencies = _read_dependencies(raw, {i["key"] for i in items})

    if len(weeks) != requested_weeks:
        checks.append(
            Check(
                key="duration",
                label="Duration",
                status=CHECK_BLOCKED,
                value=f"{len(weeks)} weeks, not {requested_weeks}",
                detail=(
                    "The import produced a different number of weeks than you asked "
                    "for. Retry the import rather than creating a plan of the wrong "
                    "length."
                ),
            )
        )
    else:
        weeks_label = f"{len(weeks)} weeks · {len(phases)} phases"
        outside = not TYPICAL_MIN_WEEKS <= len(weeks) <= TYPICAL_MAX_WEEKS
        checks.append(
            Check(
                key="duration",
                label="Duration",
                status=CHECK_OK,
                value=weeks_label,
                detail=(
                    "Outside the usual 8-12 weeks; the plan screens still work but will scroll."
                    if outside
                    else ""
                ),
            )
        )

    checks.append(_capacity_check(items, weeks, weekly_capacity_minutes))
    checks.append(_deadline_check(weeks, mode, deadline, start_date))
    checks.append(_estimate_check(items, resolutions))
    checks.append(_coverage_check(raw, items, resolutions))
    checks.append(_retrieval_check(items, resolutions))
    checks.append(_dependency_check(dependencies, resolutions))
    checks.append(_title_check(phases, weeks))
    checks.append(_core_check(items))

    subject_slug = str(raw.get("subject_slug", "")).strip().casefold()
    supports_cards = subject_supports_cards(
        subject_slug, bool(raw.get("supports_technical_recall_cards"))
    )

    preview = {
        "subject": str(raw.get("subject", "")).strip() or "Study plan",
        "subject_slug": subject_slug,
        "title": str(raw.get("plan_title", "")).strip() or "Study plan",
        # Carried on the preview so plan creation reads one object and the stored
        # `guide_text` is byte-identical to the string the offsets were checked
        # against. Anything else risks offsets that resolve against a different
        # string than the one they were validated on.
        "guide_text": guide_text,
        "supports_recall_cards": supports_cards,
        "requested_weeks": requested_weeks,
        "weekly_capacity_minutes": weekly_capacity_minutes,
        "mode": mode,
        "deadline": deadline.isoformat() if deadline else None,
        "start_date": start_date.isoformat(),
        "phases": phases,
        "weeks": weeks,
        "items": items,
        "dependencies": dependencies,
        "unresolved_estimates": [
            str(k) for k in raw.get("unresolved_estimates", []) if isinstance(k, str)
        ],
        "possible_omissions": [o for o in raw.get("possible_omissions", []) if isinstance(o, dict)],
        "resolutions": resolutions,
        "total_minutes": sum(i["estimate_minutes"] for i in items),
        "forecast_end_plan_week": max((w["index"] for w in weeks), default=0),
    }
    return ImportResult(preview=preview, checks=checks)


# --- readers ----------------------------------------------------------------


def _read_phases(raw: Mapping[str, Any]) -> list[dict[str, Any]]:
    entries = raw.get("phases")
    if not isinstance(entries, list) or not entries:
        raise ImportError_("the import produced no phases")
    phases = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        try:
            index = int(entry["index"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ImportError_("a phase is missing its index") from exc
        phases.append(
            {
                "index": index,
                "full_title": str(entry.get("full_title", "")).strip(),
                "overview_title": str(entry.get("overview_title", "")).strip(),
                "description": str(entry.get("description", "")).strip(),
            }
        )
    phases.sort(key=lambda p: p["index"])
    if [p["index"] for p in phases] != list(range(1, len(phases) + 1)):
        raise ImportError_("phase indexes are not 1..N without gaps")
    return phases


def _read_weeks(
    raw: Mapping[str, Any], phases: Sequence[Mapping[str, Any]]
) -> list[dict[str, Any]]:
    entries = raw.get("weeks")
    if not isinstance(entries, list) or not entries:
        raise ImportError_("the import produced no weeks")
    known_phases = {p["index"] for p in phases}
    weeks = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        try:
            index = int(entry["index"])
            phase_index = int(entry["phase_index"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ImportError_("a week is missing its index or phase") from exc
        if phase_index not in known_phases:
            raise ImportError_(f"week {index} points at phase {phase_index}, which does not exist")
        weeks.append(
            {
                "index": index,
                "phase_index": phase_index,
                "full_title": str(entry.get("full_title", "")).strip(),
                "overview_title": str(entry.get("overview_title", "")).strip(),
            }
        )
    weeks.sort(key=lambda w: w["index"])
    if [w["index"] for w in weeks] != list(range(1, len(weeks) + 1)):
        raise ImportError_("week indexes are not 1..N without gaps")

    # Phases must own contiguous, ordered spans — the overview renders a week
    # range per phase, and an interleaved phase has no range to render.
    seen: list[int] = []
    for week in weeks:
        if not seen or seen[-1] != week["phase_index"]:
            if week["phase_index"] in seen:
                raise ImportError_(
                    f"phase {week['phase_index']} owns a non-contiguous run of weeks"
                )
            seen.append(week["phase_index"])
    if seen != sorted(seen):
        raise ImportError_("phases are not in week order")
    return weeks


def _read_items(
    raw: Mapping[str, Any], weeks: Sequence[Mapping[str, Any]], guide_text: str
) -> list[dict[str, Any]]:
    entries = raw.get("items")
    if not isinstance(entries, list) or not entries:
        raise ImportError_("the import produced no items")

    phase_of_week = {w["index"]: w["phase_index"] for w in weeks}
    items: list[dict[str, Any]] = []
    keys: set[str] = set()

    for entry in entries:
        if not isinstance(entry, dict):
            continue
        key = str(entry.get("key", "")).strip()
        if not key:
            raise ImportError_("an item has no key")
        if key in keys:
            raise ImportError_(f"item key {key!r} appears more than once")
        keys.add(key)

        try:
            week_index = int(entry["week_index"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ImportError_(f"item {key} has no week") from exc
        if week_index not in phase_of_week:
            raise ImportError_(f"item {key} points at week {week_index}, which does not exist")

        try:
            minutes = int(entry["estimate_minutes"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ImportError_(f"item {key} has no usable estimate") from exc

        quoted = str(entry.get("source_excerpt", "")).strip()
        start, end, offsets_ok = _normalise_offsets(
            guide_text, entry.get("source_start"), entry.get("source_end"), quoted
        )
        # Once located, the excerpt is re-read from the guide rather than kept as
        # the model wrote it. The audit screens quote this string and offer to
        # jump to it, so the text and the offsets have to be the same span — a
        # reflowed or re-punctuated quote would send the user somewhere that does
        # not look like what they were shown.
        if start is not None and end is not None:
            quoted = guide_text[start:end].strip()

        items.append(
            {
                "key": key,
                "week_index": week_index,
                "phase_index": phase_of_week[week_index],
                "guide_order": int(entry.get("guide_order", len(items) + 1)),
                "type": str(entry.get("type", "learn")),
                "priority": str(entry.get("priority", PRIORITY_CORE)),
                "full_title": str(entry.get("full_title", "")).strip(),
                "why_it_matters": str(entry.get("why_it_matters", "")).strip(),
                "done_when": str(entry.get("done_when", "")).strip(),
                # Recomputed, never taken on trust: the model is asked for
                # 30-minute increments and a CHECK constraint enforces them, so a
                # stray 45 is rounded up here rather than failing the insert.
                "estimate_minutes": _round_to_increment(minutes),
                "estimate_minutes_raw": minutes,
                "estimate_source": str(entry.get("estimate_source", ESTIMATE_IMPORTED)),
                "estimate_confidence": str(entry.get("estimate_confidence", "high")),
                "origin": str(entry.get("origin", "imported")),
                "source_item_key": (entry.get("source_item_key") or None),
                "source_start": start,
                "source_end": end,
                "source_excerpt": quoted,
                "source_offsets_ok": offsets_ok,
                "parser_interpretation": str(entry.get("parser_interpretation", "")).strip(),
            }
        )

    for item in items:
        source_key = item["source_item_key"]
        if source_key and source_key not in keys:
            # A dangling retrieval source is dropped rather than rejected: the
            # activity is still valid work, it just loses its ordering hint.
            item["source_item_key"] = None
            item["parser_interpretation"] = (
                item["parser_interpretation"]
                + " (the item this retrieves was not found in the import)"
            ).strip()

    items.sort(key=lambda i: (i["week_index"], i["guide_order"], i["key"]))
    return items


def _read_dependencies(raw: Mapping[str, Any], item_keys: set[str]) -> list[dict[str, Any]]:
    entries = raw.get("dependencies")
    if not isinstance(entries, list):
        return []
    deps = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        prereq = str(entry.get("prerequisite_key", "")).strip()
        dependent = str(entry.get("dependent_key", "")).strip()
        # A dependency naming an item that does not exist is dropped, not
        # rejected. Keeping it would either crash the scheduler or silently
        # constrain nothing; dropping it is visible in the dependency audit.
        if prereq not in item_keys or dependent not in item_keys or prereq == dependent:
            continue
        deps.append(
            {
                "prerequisite_key": prereq,
                "dependent_key": dependent,
                "kind": str(entry.get("kind", "hard")),
                "source": str(entry.get("source", DEP_IMPORTED)),
                "confidence": str(entry.get("confidence", "high")),
                "rationale": str(entry.get("rationale", "")).strip(),
                "source_excerpt": str(entry.get("source_excerpt", "")).strip(),
            }
        )
    return deps


def _round_to_increment(minutes: int) -> int:
    if minutes <= 0:
        return ESTIMATE_INCREMENT
    remainder = minutes % ESTIMATE_INCREMENT
    return minutes if remainder == 0 else minutes + (ESTIMATE_INCREMENT - remainder)


# --- individual checks ------------------------------------------------------


def _capacity_check(items, weeks, capacity) -> Check:
    """Recomputed from the item estimates, never read off the model's answer."""
    loads: dict[int, int] = {w["index"]: 0 for w in weeks}
    for item in items:
        loads[item["week_index"]] += item["estimate_minutes"]
    over = sorted(idx for idx, minutes in loads.items() if minutes > capacity)
    if not over:
        busiest = max(loads.values(), default=0)
        return Check(
            key="capacity",
            label="Capacity",
            status=CHECK_OK,
            value="Fits",
            detail=(
                f"Busiest week is {sched.format_hours(busiest)} of {sched.format_hours(capacity)}."
            ),
        )
    worst = max(loads[idx] for idx in over)
    return Check(
        key="capacity",
        label="Capacity",
        status=CHECK_NEEDS_REVIEW,
        value=f"{len(over)} week{'s' if len(over) > 1 else ''} over",
        detail=(
            f"Week {over[0]} is the first over its {sched.format_hours(capacity)} "
            f"budget, and the busiest is {sched.format_hours(worst)}. Raise the "
            "weekly capacity, lengthen the plan, or edit the estimates."
        ),
        destination="capacity",
        item_keys=[str(i) for i in over],
    )


def _deadline_check(weeks, mode, deadline, start_date) -> Check:
    """A fixed deadline is a ceiling. Finishing before it is not a problem."""
    if mode != MODE_FIXED or deadline is None:
        return Check(
            key="deadline",
            label="Deadline",
            status=CHECK_OK,
            value="Flexible",
            detail="No fixed date to hit.",
        )
    plan = sched.SchedPlan(
        mode=mode,
        start_date=start_date,
        default_weekly_capacity_minutes=1,
        deadline=deadline,
    )
    last_week = max((w["index"] for w in weeks), default=1)
    ends = sched.week_end_date(plan, last_week)
    if ends <= deadline:
        return Check(
            key="deadline",
            label="Deadline",
            status=CHECK_OK,
            value=f"Fits before {deadline.day} {deadline.strftime('%b')}",
            detail=f"The last plan week ends {ends.day} {ends.strftime('%b')}.",
        )
    return Check(
        key="deadline",
        label="Deadline",
        status=CHECK_NEEDS_REVIEW,
        value=f"Ends after {deadline.day} {deadline.strftime('%b')}",
        detail=(
            f"The last plan week ends {ends.day} {ends.strftime('%b')}, "
            f"{(ends - deadline).days} days late. Shorten the plan or move the date."
        ),
        destination="deadline",
    )


def _estimate_check(items, resolutions) -> Check:
    flagged = [
        i["key"]
        for i in items
        if i["estimate_confidence"] == CONFIDENCE_NEEDS_REVIEW
        or i["estimate_minutes"] != i["estimate_minutes_raw"]
    ]
    reviewed = set(resolutions.get("estimates_reviewed", []))
    outstanding = [k for k in flagged if k not in reviewed]
    if not outstanding:
        return Check(
            key="estimates",
            label="Workload estimates",
            status=CHECK_OK,
            value="Reviewed" if flagged else "Confident",
        )
    return Check(
        key="estimates",
        label="Workload estimates",
        status=CHECK_NEEDS_REVIEW,
        value=f"{len(outstanding)} need review",
        detail="Estimated from scope rather than read from the guide.",
        destination="estimate-audit",
        item_keys=outstanding,
    )


def _coverage_check(raw, items, resolutions) -> Check:
    omissions = [o for o in raw.get("possible_omissions", []) if isinstance(o, dict)]
    bad_offsets = [i["key"] for i in items if not i["source_offsets_ok"]]
    acknowledged = bool(resolutions.get("omissions_acknowledged"))

    if bad_offsets:
        return Check(
            key="coverage",
            label="Source coverage",
            status=CHECK_NEEDS_REVIEW,
            value=f"{len(bad_offsets)} source link{'s' if len(bad_offsets) > 1 else ''} broken",
            detail=(
                "These items quote the guide but their offsets do not match it. "
                "The text is still correct; the jump-to-source link is not."
            ),
            destination="source-coverage",
            item_keys=bad_offsets,
        )
    if omissions and not acknowledged:
        return Check(
            key="coverage",
            label="Source coverage",
            status=CHECK_NEEDS_REVIEW,
            value=f"{len(omissions)} possible omission{'s' if len(omissions) > 1 else ''}",
            detail="Parts of the guide the import could not place.",
            destination="source-coverage",
        )
    return Check(key="coverage", label="Source coverage", status=CHECK_OK, value="Complete")


def _retrieval_check(items, resolutions) -> Check:
    """Generated retrieval must be approved in Preview or it never reaches a plan."""
    generated = [
        i["key"] for i in items if i["type"] == ITEM_RETRIEVE and i["origin"] == ORIGIN_GENERATED
    ]
    approved = set(resolutions.get("retrieval_approved", []))
    rejected = set(resolutions.get("retrieval_rejected", []))
    outstanding = [k for k in generated if k not in approved and k not in rejected]
    if not outstanding:
        imported = sum(
            1 for i in items if i["type"] == ITEM_RETRIEVE and i["origin"] != ORIGIN_GENERATED
        )
        return Check(
            key="retrieval",
            label="Retrieval activities",
            status=CHECK_OK,
            value=f"{imported + len(approved)} in the plan",
            detail="Retrieval consumes plan capacity and doesn't block a week.",
        )
    return Check(
        key="retrieval",
        label="Retrieval activities",
        status=CHECK_NEEDS_REVIEW,
        value=f"{len(outstanding)} need review",
        detail="Proposed by the import rather than found in the guide.",
        destination="retrieval-audit",
        item_keys=outstanding,
    )


def _dependency_check(dependencies, resolutions) -> Check:
    """Strong imported ordering is accepted automatically and never shown.

    Only uncertain, contradictory, or schedule-changing links reach the audit —
    putting 31 obvious prerequisites in front of the user teaches them to tap
    through the one that mattered.
    """
    confirmed = set(resolutions.get("dependencies_confirmed", []))
    needs_confirmation = [
        f"{d['prerequisite_key']}->{d['dependent_key']}"
        for d in dependencies
        if not (d["source"] == DEP_IMPORTED and d["confidence"] == "high")
    ]
    outstanding = [d for d in needs_confirmation if d not in confirmed]
    imported = sum(1 for d in dependencies if d["source"] == DEP_IMPORTED)
    inferred = len(dependencies) - imported
    if not outstanding:
        return Check(
            key="dependencies",
            label="Dependencies",
            status=CHECK_OK,
            value="Confirmed",
            detail=f"{imported} imported · {inferred} inferred",
        )
    return Check(
        key="dependencies",
        label="Dependencies",
        status=CHECK_NEEDS_REVIEW,
        value=f"{len(outstanding)} need{'s' if len(outstanding) == 1 else ''} confirmation",
        detail=f"{imported} imported · {inferred} inferred",
        destination="dependency-audit",
        item_keys=outstanding,
    )


def _title_check(phases, weeks) -> Check:
    problems: list[str] = []
    for phase in phases:
        problem = overview_title_problem(phase["overview_title"])
        if problem:
            problems.append(f"Phase {phase['index']} short title {problem}")

    by_phase: dict[int, list[str]] = {}
    for week in weeks:
        problem = overview_title_problem(week["overview_title"])
        if problem:
            problems.append(f"Week {week['index']} short title {problem}")
        by_phase.setdefault(week["phase_index"], []).append(week["overview_title"].casefold())
    for phase_index, titles in by_phase.items():
        duplicates = {t for t in titles if titles.count(t) > 1 and t}
        for title in sorted(duplicates):
            problems.append(f"Phase {phase_index} has two weeks called {title!r}")

    if not problems:
        return Check(key="titles", label="Short titles", status=CHECK_OK, value="Generated")
    return Check(
        key="titles",
        label="Short titles",
        status=CHECK_NEEDS_REVIEW,
        value=f"{len(problems)} to edit",
        detail=" · ".join(problems[:3]),
        destination="titles",
    )


def _core_check(items) -> Check:
    core = [i for i in items if i["priority"] == PRIORITY_CORE]
    if core:
        return Check(
            key="core",
            label="Core work",
            status=CHECK_OK,
            value=f"{len(core)} of {len(items)} items",
        )
    return Check(
        key="core",
        label="Core work",
        status=CHECK_BLOCKED,
        value="None found",
        detail=(
            "Every item was marked optional or recurring, so the plan has nothing "
            "that must happen. Retry the import."
        ),
    )


# --- preview to rows --------------------------------------------------------


def build_plan_rows(preview: Mapping[str, Any], *, start_date: date) -> dict[str, Any]:
    """Turn a validated, resolved preview into ORM rows. No model output involved.

    Generated retrieval that the user did not approve is dropped here, which is
    the enforcement point for "must be approved during Preview" — an unapproved
    activity has no row to be created from.
    """
    resolutions = dict(preview.get("resolutions", {}))
    approved = set(resolutions.get("retrieval_approved", []))

    def included(item: Mapping[str, Any]) -> bool:
        if item["type"] == ITEM_RETRIEVE and item["origin"] == ORIGIN_GENERATED:
            return item["key"] in approved
        return True

    items_in = [i for i in preview["items"] if included(i)]
    weeks_in = preview["weeks"]

    deadline = preview.get("deadline")
    plan = StudyPlan(
        title=preview["title"],
        subject=preview["subject"],
        subject_slug=preview["subject_slug"],
        guide_text=preview["guide_text"],
        mode=preview["mode"],
        deadline=date.fromisoformat(deadline) if deadline else None,
        start_date=start_date,
        default_weekly_capacity_minutes=preview["weekly_capacity_minutes"],
        current_week_index=1,
        forecast_end_plan_week=max((w["index"] for w in weeks_in), default=1),
    )

    phase_rows = {
        p["index"]: StudyPlanPhase(
            plan_id=plan.id,
            index=p["index"],
            full_title=p["full_title"],
            overview_title=p["overview_title"],
            description=p["description"],
        )
        for p in preview["phases"]
    }
    week_rows = {
        w["index"]: StudyPlanWeek(
            plan_id=plan.id,
            phase_id=phase_rows[w["phase_index"]].id,
            index=w["index"],
            full_title=w["full_title"],
            overview_title=w["overview_title"],
        )
        for w in weeks_in
    }

    now_approved = _utcnow()
    item_rows: dict[str, StudyPlanItem] = {}
    for entry in items_in:
        week = week_rows[entry["week_index"]]
        item_rows[entry["key"]] = StudyPlanItem(
            plan_id=plan.id,
            phase_id=week.phase_id,
            week_id=week.id,
            guide_order=entry["guide_order"],
            type=entry["type"],
            priority=entry["priority"],
            full_title=entry["full_title"],
            why_it_matters=entry["why_it_matters"],
            done_when=entry["done_when"],
            estimate_minutes=entry["estimate_minutes"],
            estimate_source=entry["estimate_source"],
            estimate_confidence=entry["estimate_confidence"],
            origin=entry["origin"],
            source_start=entry["source_start"],
            source_end=entry["source_end"],
            source_excerpt=entry["source_excerpt"],
            parser_interpretation=entry["parser_interpretation"],
            approved_at=now_approved if entry["origin"] == ORIGIN_GENERATED else None,
        )

    for entry in items_in:
        source_key = entry.get("source_item_key")
        if source_key and source_key in item_rows:
            item_rows[entry["key"]].source_item_id = item_rows[source_key].id

    confirmed = set(resolutions.get("dependencies_confirmed", []))
    dependency_rows = []
    for dep in preview["dependencies"]:
        prereq = item_rows.get(dep["prerequisite_key"])
        dependent = item_rows.get(dep["dependent_key"])
        if prereq is None or dependent is None:
            continue
        edge = f"{dep['prerequisite_key']}->{dep['dependent_key']}"
        auto = dep["source"] == DEP_IMPORTED and dep["confidence"] == "high"
        dependency_rows.append(
            StudyPlanItemDependency(
                plan_id=plan.id,
                prerequisite_item_id=prereq.id,
                dependent_item_id=dependent.id,
                kind=dep["kind"],
                source=dep["source"],
                confidence=dep["confidence"],
                rationale=dep["rationale"],
                source_excerpt=dep["source_excerpt"],
                confirmed_at=now_approved if (auto or edge in confirmed) else None,
            )
        )

    return {
        "plan": plan,
        "phases": list(phase_rows.values()),
        "weeks": list(week_rows.values()),
        "items": list(item_rows.values()),
        "dependencies": dependency_rows,
    }


def _utcnow() -> datetime:
    return datetime.now(UTC)


def default_start_date(today: date) -> date:
    """Plans start on the Monday of the current week, so week 1 reads naturally."""
    return today - timedelta(days=today.weekday())


__all__ = [
    "CHECK_BLOCKED",
    "CHECK_NEEDS_REVIEW",
    "CHECK_OK",
    "Check",
    "ImportError_",
    "ImportResult",
    "build_plan_rows",
    "default_start_date",
    "overview_title_problem",
    "validate_import",
    "validate_request",
]
