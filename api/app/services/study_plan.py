"""Study Plan domain logic: lifecycle, revisions, the card gate, duplicates.

Everything that decides *what should happen* lives here; the router decides how
to say it over HTTP and `study_plan_scheduler` decides where work lands. The
split matters because the safety boundary is enforced in this module: no
function below writes to `cards`, `sessions`, or any SM-2 field except
`accept_card_proposals`, which is the one path the design authorises to create
cards and does so in a single transaction after an explicit confirmation.
"""

import hashlib
import json
import re
import unicodedata
import uuid
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict
from datetime import UTC, date, datetime
from typing import Any

from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.models import (
    DEP_HARD,
    DISPOSITION_EXISTING,
    DISPOSITION_NOT_SUGGESTED,
    DISPOSITION_POSSIBLE_OVERLAP,
    DISPOSITION_SUGGESTED,
    DUPLICATE_EXACT,
    DUPLICATE_NONE,
    DUPLICATE_POSSIBLE,
    FOUNDER_USER_ID,
    ITEM_COMPLETE,
    ITEM_PENDING,
    MODE_FIXED,
    PLAN_ACTIVE,
    PLAN_ARCHIVED,
    PLAN_COMPLETED,
    PLAN_PAUSED,
    PRIORITY_CORE,
    Card,
    StudyPlan,
    StudyPlanCardProposal,
    StudyPlanItem,
    StudyPlanItemDependency,
    StudyPlanPhase,
    StudyPlanRevision,
    StudyPlanWeek,
)
from app.services import study_plan_scheduler as sched

# Subjects the existing three-axis technical rubric can actually grade. Law,
# anatomy, and language plans use plan-local retrieval and create no cards — the
# rubric scores mechanism accuracy, trade-off awareness, and failure-mode
# awareness, none of which mean anything for a vocabulary list or a case holding.
#
# Matching is by token rather than by exact slug. A live import of
# `docs/CURRICULUM.md` returned `senior-backend-interview-prep`, which an exact
# allowlist rejected — the flagship use case, refused on a slug variant. The
# model has no way to know which strings are on a list it cannot see, so the
# list is of *words* and the slug is checked against them.
TECHNICAL_SUBJECT_TOKENS = frozenset(
    {
        "algorithms",
        "api",
        "architecture",
        "backend",
        "cloud",
        "computing",
        "concurrency",
        "database",
        "databases",
        "devops",
        "distributed",
        "engineering",
        "frontend",
        "infrastructure",
        "kubernetes",
        "microservices",
        "networking",
        "observability",
        "operating",
        "platform",
        "programming",
        "reliability",
        "scalability",
        "security",
        "software",
        "sre",
        "system",
        "systems",
    }
)

# The deny list wins outright. A guide whose subject names one of these is not a
# software-engineering plan however many technical words it also contains, and
# "anatomy of distributed systems" is not worth being clever about — the cost of
# a false positive is recall cards the scoring rubric cannot grade.
NON_TECHNICAL_SUBJECT_TOKENS = frozenset(
    {
        "anatomy",
        "biology",
        "chemistry",
        "clinical",
        "constitutional",
        "criminal",
        "economics",
        "finance",
        "french",
        "german",
        "history",
        "japanese",
        "language",
        "law",
        "legal",
        "literature",
        "mandarin",
        "medicine",
        "medical",
        "nursing",
        "pharmacology",
        "philosophy",
        "physiology",
        "psychology",
        "spanish",
        "vocabulary",
    }
)

_SLUG_SPLIT = re.compile(r"[^a-z0-9]+")

# The curriculum-honesty gate (V3.4 §6). All five, in order. Failing any one
# makes a candidate not selectable and not counted — there is no bypass and no
# "add back", because a gate with an override is a suggestion.
GATE_QUESTIONS = (
    "What source lesson or observed failure justifies the card?",
    "Can its mechanism be reconstructed in under two minutes?",
    "Does it test a scenario rather than a definition?",
    "Would a different answer change an interview decision or reveal a real gap?",
    "Is it more useful than spending the same review budget on an existing weak card?",
)

# At most three, and low confidence produces none. The budget being competed for
# is the user's review time, not screen space.
MAX_SUGGESTED_CARDS = 3


def _now() -> datetime:
    return datetime.now(UTC)


# --- duplicate checking -----------------------------------------------------

# Punctuation stripped before comparing topics. Deliberately small and written
# down rather than "all punctuation": hyphens and slashes carry meaning in topic
# names ("read-your-writes", "CI/CD"), so they are normalised to a single space
# rather than deleted, and everything else in this set is packaging.
_STRIP_PUNCTUATION = str.maketrans({ch: None for ch in "\"'`.,;:!?()[]{}«»‘’“”"})
_SEPARATORS = re.compile(r"[-–—_/\\|]+")
_WHITESPACE = re.compile(r"\s+")


def normalize_topic(topic: str) -> str:
    """The deterministic key an exact duplicate is decided on.

    Trim, casefold, fold separators to spaces, drop packaging punctuation,
    collapse internal whitespace. Unicode is NFKC-normalised first so a curly
    apostrophe and a straight one cannot produce two "different" cards.

    This is the authoritative check. Any semantic-overlap signal is a warning
    shown to the user, never a decision — see `classify_duplicate`.
    """
    text = unicodedata.normalize("NFKC", topic).strip().casefold()
    text = _SEPARATORS.sub(" ", text)
    text = text.translate(_STRIP_PUNCTUATION)
    return _WHITESPACE.sub(" ", text).strip()


async def normalized_card_index(
    db: AsyncSession, user_id: uuid.UUID = FOUNDER_USER_ID
) -> dict[str, Card]:
    """Every card keyed by its normalized topic, read once.

    Normalization is not expressible in portable SQL, so the comparison happens
    in Python — but it only needs the card list once per request, not once per
    candidate. Later collisions lose to the earlier card, which is the one that
    already exists.
    """
    index: dict[str, Card] = {}
    for card in (await db.exec(select(Card).where(Card.user_id == user_id))).all():
        index.setdefault(normalize_topic(card.topic), card)
    return index


def classify_duplicate(
    exact: Card | None, semantic_card_id: uuid.UUID | None
) -> tuple[str, uuid.UUID | None]:
    """Exact match wins. A semantic hint only ever downgrades to a user choice."""
    if exact is not None:
        return DUPLICATE_EXACT, exact.id
    if semantic_card_id is not None:
        return DUPLICATE_POSSIBLE, semantic_card_id
    return DUPLICATE_NONE, None


# --- subject eligibility ----------------------------------------------------


def subject_tokens(subject_slug: str) -> set[str]:
    return {t for t in _SLUG_SPLIT.split(subject_slug.casefold()) if t}


def slug_supports_cards(subject_slug: str) -> bool:
    """Does this subject slug name something the technical rubric can grade?

    The deny list wins: `anatomy-of-distributed-systems` is refused however many
    technical words it also contains.
    """
    tokens = subject_tokens(subject_slug)
    if tokens & NON_TECHNICAL_SUBJECT_TOKENS:
        return False
    return bool(tokens & TECHNICAL_SUBJECT_TOKENS)


def subject_supports_cards(subject_slug: str, importer_says_supported: bool) -> bool:
    """Two keys, and a veto — the rule applied once, at import.

    Only the importer calls this, because it is the only caller that has the
    model's answer. Everything after creation reads the slug through
    `slug_supports_cards`, which is what the stored plan can actually be
    re-derived from.
    """
    return bool(importer_says_supported) and slug_supports_cards(subject_slug)


# --- the gate ---------------------------------------------------------------


def normalise_gate_results(raw: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Force the model's gate answers into exactly five ordered results.

    A missing or malformed answer is a **failure**, not a pass: the gate exists
    to stop weak cards, so an unparseable answer must not open the door.
    """
    by_index = {}
    for entry in raw:
        try:
            index = int(entry.get("question_index", 0)) - 1
        except (TypeError, ValueError):
            continue
        if 0 <= index < len(GATE_QUESTIONS):
            by_index[index] = entry

    results = []
    for index, question in enumerate(GATE_QUESTIONS):
        entry = by_index.get(index, {})
        results.append(
            {
                "question_index": index + 1,
                "question": question,
                "passed": bool(entry.get("passed", False)),
                "reason": str(entry.get("reason", "")).strip()
                or "The importer gave no answer for this question.",
            }
        )
    return results


def gate_passed(gate_results: Sequence[Mapping[str, Any]]) -> bool:
    return len(gate_results) == len(GATE_QUESTIONS) and all(
        bool(r.get("passed")) for r in gate_results
    )


def first_failed_gate(
    gate_results: Sequence[Mapping[str, Any]],
) -> Mapping[str, Any] | None:
    return next((r for r in gate_results if not r.get("passed")), None)


def disposition_for(gate_results: Sequence[Mapping[str, Any]], duplicate_result: str) -> str:
    """What the card-suggestions screen does with this candidate.

    Order matters: an exact duplicate is `existing` even if the gate passed,
    because the card already exists and re-proposing it is the bug. A failed
    gate is `not_suggested` and carries no action at all.
    """
    if duplicate_result == DUPLICATE_EXACT:
        return DISPOSITION_EXISTING
    if not gate_passed(gate_results):
        return DISPOSITION_NOT_SUGGESTED
    if duplicate_result == DUPLICATE_POSSIBLE:
        return DISPOSITION_POSSIBLE_OVERLAP
    return DISPOSITION_SUGGESTED


def selectable(proposal: StudyPlanCardProposal) -> bool:
    """Only a clean 5-of-5 candidate can be added, and only these are counted."""
    return proposal.disposition in (DISPOSITION_SUGGESTED, DISPOSITION_POSSIBLE_OVERLAP)


def request_hash(selected_ids: Iterable[uuid.UUID], edits: Mapping[str, Any]) -> str:
    """Stable hash of what an acceptance was asked to do.

    Sorted ids and sorted JSON keys, so the same intent always hashes the same
    and a retry is recognisable as a replay rather than a new batch.
    """
    payload = {
        "selected": sorted(str(i) for i in selected_ids),
        "edits": json.loads(json.dumps(edits, sort_keys=True, default=str)),
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode()).hexdigest()


# --- ORM to scheduler -------------------------------------------------------


def to_sched_plan(plan: StudyPlan) -> sched.SchedPlan:
    return sched.SchedPlan(
        mode=plan.mode,
        start_date=plan.start_date,
        default_weekly_capacity_minutes=plan.default_weekly_capacity_minutes,
        current_week_index=plan.current_week_index,
        deadline=plan.deadline,
        revision=plan.revision,
    )


def to_sched_weeks(
    weeks: Sequence[StudyPlanWeek], phases: Sequence[StudyPlanPhase]
) -> list[sched.SchedWeek]:
    phase_index = {p.id: p.index for p in phases}
    return [
        sched.SchedWeek(
            index=w.index,
            phase_index=phase_index.get(w.phase_id, 1),
            override_capacity_minutes=w.override_capacity_minutes,
        )
        for w in sorted(weeks, key=lambda w: w.index)
    ]


def to_sched_items(
    items: Sequence[StudyPlanItem],
    weeks: Sequence[StudyPlanWeek],
    phases: Sequence[StudyPlanPhase],
) -> list[sched.SchedItem]:
    week_index = {w.id: w.index for w in weeks}
    phase_index = {p.id: p.index for p in phases}
    return [
        sched.SchedItem(
            id=str(i.id),
            phase_index=phase_index.get(i.phase_id, 1),
            week_index=week_index.get(i.week_id, 1),
            guide_order=i.guide_order,
            estimate_minutes=i.estimate_minutes,
            priority=i.priority,
            status=i.status,
            type=i.type,
            source_item_id=str(i.source_item_id) if i.source_item_id else None,
            title=i.full_title,
        )
        for i in items
    ]


def to_sched_dependencies(
    deps: Sequence[StudyPlanItemDependency],
) -> list[sched.SchedDependency]:
    return [
        sched.SchedDependency(
            prerequisite_item_id=str(d.prerequisite_item_id),
            dependent_item_id=str(d.dependent_item_id),
            kind=d.kind,
        )
        for d in deps
    ]


class PlanGraph:
    """Everything the scheduler needs about one plan, loaded once."""

    def __init__(
        self,
        plan: StudyPlan,
        phases: list[StudyPlanPhase],
        weeks: list[StudyPlanWeek],
        items: list[StudyPlanItem],
        dependencies: list[StudyPlanItemDependency],
    ) -> None:
        self.plan = plan
        self.phases = sorted(phases, key=lambda p: p.index)
        self.weeks = sorted(weeks, key=lambda w: w.index)
        self.items = items
        self.dependencies = dependencies
        self.week_by_index = {w.index: w for w in self.weeks}
        self.week_by_id = {w.id: w for w in self.weeks}
        self.phase_by_id = {p.id: p for p in self.phases}
        self.item_by_id = {i.id: i for i in items}

    def items_in(self, week: StudyPlanWeek) -> list[StudyPlanItem]:
        return sorted((i for i in self.items if i.week_id == week.id), key=lambda i: i.guide_order)

    def phase_of_week(self, week: StudyPlanWeek) -> StudyPlanPhase | None:
        return self.phase_by_id.get(week.phase_id)

    def sched_inputs(self):
        return (
            to_sched_plan(self.plan),
            to_sched_weeks(self.weeks, self.phases),
            to_sched_items(self.items, self.weeks, self.phases),
            to_sched_dependencies(self.dependencies),
        )


async def load_plan_graph(db: AsyncSession, plan: StudyPlan) -> PlanGraph:
    phases = (await db.exec(select(StudyPlanPhase).where(StudyPlanPhase.plan_id == plan.id))).all()
    weeks = (await db.exec(select(StudyPlanWeek).where(StudyPlanWeek.plan_id == plan.id))).all()
    items = (await db.exec(select(StudyPlanItem).where(StudyPlanItem.plan_id == plan.id))).all()
    deps = (
        await db.exec(
            select(StudyPlanItemDependency).where(StudyPlanItemDependency.plan_id == plan.id)
        )
    ).all()
    return PlanGraph(plan, list(phases), list(weeks), list(items), list(deps))


async def insert_plan_rows(
    db: AsyncSession, plan: StudyPlan, rows: Mapping[str, Sequence[Any]]
) -> None:
    """Insert a whole plan graph in foreign-key order.

    These models deliberately have no SQLAlchemy relationships. Without the
    flushes, Postgres can receive items before their phases or weeks even though
    the table-level foreign keys are correct. Every creation path, including
    deterministic first-party seeds, shares this helper.
    """
    db.add(plan)
    await db.flush()
    for key in ("phases", "weeks", "items"):
        for row in rows[key]:
            db.add(row)
        await db.flush()
    for row in rows["dependencies"]:
        db.add(row)
    await db.flush()


def build_proposal_for(graph: PlanGraph, **kwargs) -> sched.Proposal:
    plan, weeks, items, deps = graph.sched_inputs()
    return sched.build_proposal(plan, weeks, items, deps, **kwargs)


# --- persistence ------------------------------------------------------------


def proposal_snapshot(proposal: sched.Proposal) -> dict[str, Any]:
    """A revision's `after` payload. Plain JSON so it survives a schema change."""
    return {
        "weeks": [asdict(w) for w in proposal.weeks],
        "moves": [asdict(m) for m in proposal.moves],
        "unresolved": [asdict(u) for u in proposal.unresolved],
        "unresolved_minutes": proposal.unresolved_minutes,
        "forecast_end_plan_week": proposal.forecast_end_plan_week,
        "deferred_item_ids": list(proposal.deferred_item_ids),
    }


def plan_snapshot(graph: PlanGraph) -> dict[str, Any]:
    """A revision's `before` payload: enough to explain what changed."""
    plan = graph.plan
    return {
        "revision": plan.revision,
        "status": plan.status,
        "current_week_index": plan.current_week_index,
        "forecast_end_plan_week": plan.forecast_end_plan_week,
        "default_weekly_capacity_minutes": plan.default_weekly_capacity_minutes,
        "weeks": [
            {
                "index": w.index,
                "override_capacity_minutes": w.override_capacity_minutes,
                "minutes": sum(
                    i.estimate_minutes
                    for i in graph.items_in(w)
                    if i.status in (ITEM_PENDING, ITEM_COMPLETE)
                ),
            }
            for w in graph.weeks
        ],
        "placements": {
            str(i.id): graph.week_by_id[i.week_id].index
            for i in graph.items
            if i.week_id in graph.week_by_id
        },
    }


def record_revision(
    db: AsyncSession,
    graph: PlanGraph,
    *,
    kind: str,
    before: dict[str, Any],
    after: dict[str, Any],
    summary: str = "",
    reversible: bool = False,
) -> StudyPlanRevision:
    revision = StudyPlanRevision(
        plan_id=graph.plan.id,
        kind=kind,
        base_plan_revision=graph.plan.revision,
        before=before,
        after=after,
        summary=summary,
        reversible=reversible,
    )
    db.add(revision)
    return revision


def apply_proposal(
    db: AsyncSession,
    graph: PlanGraph,
    proposal: sched.Proposal,
    *,
    kind: str,
    summary: str = "",
    capacity_overrides: Mapping[int, int | None] | None = None,
    default_capacity_minutes: int | None = None,
    reversible: bool = False,
) -> StudyPlanRevision:
    """Persist a validated proposal. The caller owns the transaction.

    Bumps `plan.revision`, so any other proposal computed against the old value
    is now stale and its Apply is a 409 rather than a silent overwrite.
    """
    before = plan_snapshot(graph)
    plan = graph.plan
    now = _now()

    if capacity_overrides:
        for index, value in capacity_overrides.items():
            week = graph.week_by_index.get(index)
            if week is not None:
                week.override_capacity_minutes = value
                week.updated_at = now
                db.add(week)
    if default_capacity_minutes is not None:
        plan.default_weekly_capacity_minutes = default_capacity_minutes

    for item in graph.items:
        target_index = proposal.placements.get(str(item.id))
        if target_index is None:
            continue
        week = graph.week_by_index.get(target_index)
        if week is not None and item.week_id != week.id:
            item.week_id = week.id
            item.phase_id = week.phase_id
            item.updated_at = now
            db.add(item)

    plan.forecast_end_plan_week = proposal.forecast_end_plan_week
    plan.revision += 1
    plan.updated_at = now
    db.add(plan)

    return record_revision(
        db,
        graph,
        kind=kind,
        before=before,
        after=proposal_snapshot(proposal),
        summary=summary,
        reversible=reversible,
    )


# --- lifecycle --------------------------------------------------------------


async def active_plan(db: AsyncSession, user_id: uuid.UUID = FOUNDER_USER_ID) -> StudyPlan | None:
    """Zero active plans is a valid state, not an error."""
    return (
        await db.exec(
            select(StudyPlan).where(StudyPlan.user_id == user_id, StudyPlan.status == PLAN_ACTIVE)
        )
    ).first()


async def make_active(
    db: AsyncSession, graph: PlanGraph, *, kind: str, summary: str
) -> StudyPlanRevision:
    """Make this the active plan, pausing whichever one currently is.

    The partial unique index only *detects* two active plans; the flush below is
    what avoids them. The index is evaluated per statement, so the incumbent's
    pause has to reach the database before this plan's activation does — and
    that ordering was previously hand-rolled at three call sites, which is two
    more than an invariant should have.
    """
    before = plan_snapshot(graph)
    now = _now()

    current = await active_plan(db, graph.plan.user_id)
    if current is not None and current.id != graph.plan.id:
        current.status = PLAN_PAUSED
        current.paused_at = now
        current.updated_at = now
        db.add(current)
        await db.flush()

    graph.plan.status = PLAN_ACTIVE
    graph.plan.paused_at = None
    graph.plan.archived_at = None
    graph.plan.revision += 1
    graph.plan.updated_at = now
    db.add(graph.plan)
    return record_revision(
        db,
        graph,
        kind=kind,
        before=before,
        after={"status": PLAN_ACTIVE},
        summary=summary,
    )


def pause(db: AsyncSession, graph: PlanGraph) -> StudyPlanRevision:
    """Freeze progression. Dates do not move by themselves."""
    before = plan_snapshot(graph)
    plan = graph.plan
    plan.status = PLAN_PAUSED
    plan.paused_at = _now()
    plan.updated_at = plan.paused_at
    db.add(plan)
    return record_revision(
        db,
        graph,
        kind="pause",
        before=before,
        after={"status": PLAN_PAUSED},
        summary="Plan paused. Local study reminders stop; no dates moved.",
        reversible=True,
    )


def complete(db: AsyncSession, graph: PlanGraph) -> StudyPlanRevision:
    """Completed is not archived — the recap is kept and listed separately."""
    before = plan_snapshot(graph)
    plan = graph.plan
    plan.status = PLAN_COMPLETED
    plan.completed_at = _now()
    plan.updated_at = plan.completed_at
    db.add(plan)
    return record_revision(
        db, graph, kind="complete", before=before, after={"status": PLAN_COMPLETED}
    )


def archive(db: AsyncSession, graph: PlanGraph) -> StudyPlanRevision:
    """An organisational action. It does not mean the work was finished."""
    before = plan_snapshot(graph)
    plan = graph.plan
    plan.status = PLAN_ARCHIVED
    plan.archived_at = _now()
    plan.updated_at = plan.archived_at
    db.add(plan)
    return record_revision(
        db, graph, kind="archive", before=before, after={"status": PLAN_ARCHIVED}
    )


def duplicate_plan(source: StudyPlan, graph: PlanGraph, *, start_date: date) -> dict[str, Any]:
    """Build the rows for a copy of `source`. Returns them; the caller adds them.

    Copies guide provenance, phase and week structure, items, dependencies,
    estimates, and user edits. Resets item completion, retrieval completion,
    dates, and reminder settings. Copies **no** cards, scores, sessions, SM-2
    state, mastery, or plan history — the duplicate starts with an empty
    revision log and no card links, which is why those tables are simply not
    read here.

    The copy is created paused. Preview runs again before it can be activated.
    """
    now = _now()
    new_plan = StudyPlan(
        user_id=source.user_id,
        title=f"{source.title} (copy)",
        subject=source.subject,
        subject_slug=source.subject_slug,
        guide_text=source.guide_text,
        status=PLAN_PAUSED,
        mode=source.mode,
        deadline=source.deadline,
        start_date=start_date,
        default_weekly_capacity_minutes=source.default_weekly_capacity_minutes,
        current_week_index=1,
        forecast_end_plan_week=source.forecast_end_plan_week,
        revision=1,
        paused_at=now,
    )

    phase_map: dict[uuid.UUID, StudyPlanPhase] = {}
    for phase in graph.phases:
        copy = StudyPlanPhase(
            plan_id=new_plan.id,
            index=phase.index,
            full_title=phase.full_title,
            overview_title=phase.overview_title,
            description=phase.description,
        )
        phase_map[phase.id] = copy

    week_map: dict[uuid.UUID, StudyPlanWeek] = {}
    for week in graph.weeks:
        copy = StudyPlanWeek(
            plan_id=new_plan.id,
            phase_id=phase_map[week.phase_id].id,
            index=week.index,
            full_title=week.full_title,
            overview_title=week.overview_title,
            override_capacity_minutes=week.override_capacity_minutes,
            # Reset: this plan has not been walked through yet.
            advanced_at=None,
        )
        week_map[week.id] = copy

    item_map: dict[uuid.UUID, StudyPlanItem] = {}
    for item in graph.items:
        copy = StudyPlanItem(
            plan_id=new_plan.id,
            phase_id=phase_map[item.phase_id].id,
            week_id=week_map[item.week_id].id,
            guide_order=item.guide_order,
            type=item.type,
            priority=item.priority,
            full_title=item.full_title,
            why_it_matters=item.why_it_matters,
            done_when=item.done_when,
            estimate_minutes=item.estimate_minutes,
            estimate_source=item.estimate_source,
            estimate_confidence=item.estimate_confidence,
            # Reset completion and its timestamps.
            status=ITEM_PENDING,
            completed_at=None,
            reopened_at=None,
            origin=item.origin,
            source_start=item.source_start,
            source_end=item.source_end,
            source_excerpt=item.source_excerpt,
            parser_interpretation=item.parser_interpretation,
            approved_at=item.approved_at,
            notes=item.notes,
            # Reset reminders: a copy must not start firing notifications.
            study_block_label=item.study_block_label,
            study_block_weekday=item.study_block_weekday,
            study_block_minute_of_day=item.study_block_minute_of_day,
            study_block_reminder_on=False,
        )
        item_map[item.id] = copy

    # Second pass: self-references resolve only once every copy exists.
    for item in graph.items:
        if item.source_item_id and item.source_item_id in item_map:
            item_map[item.id].source_item_id = item_map[item.source_item_id].id

    dependencies = [
        StudyPlanItemDependency(
            plan_id=new_plan.id,
            prerequisite_item_id=item_map[d.prerequisite_item_id].id,
            dependent_item_id=item_map[d.dependent_item_id].id,
            kind=d.kind,
            source=d.source,
            confidence=d.confidence,
            rationale=d.rationale,
            source_excerpt=d.source_excerpt,
            confirmed_at=d.confirmed_at,
        )
        for d in graph.dependencies
        if d.prerequisite_item_id in item_map and d.dependent_item_id in item_map
    ]

    return {
        "plan": new_plan,
        "phases": list(phase_map.values()),
        "weeks": list(week_map.values()),
        "items": list(item_map.values()),
        "dependencies": dependencies,
    }


# --- display helpers --------------------------------------------------------


def display_title(full_title: str, overview_title: str) -> str:
    """`overview_title ?? full_title`. Presentation only, never a logic key."""
    return overview_title.strip() or full_title


def week_range_label(weeks: Sequence[StudyPlanWeek]) -> str:
    if not weeks:
        return ""
    first, last = weeks[0].index, weeks[-1].index
    return f"Week {first}" if first == last else f"Weeks {first}–{last}"


def week_of_label(plan: StudyPlan, index: int) -> str:
    return sched.week_of_label(plan.start_date, index)


def phase_status(phase_index: int, current_phase_index: int) -> str:
    """Sentence case, per V3.5. Status is a word first and a colour second."""
    if phase_index < current_phase_index:
        return "Complete"
    if phase_index == current_phase_index:
        return "Current"
    if phase_index == current_phase_index + 1:
        return "Next"
    return "Later"


def week_status(week_index: int, current_week_index: int, all_complete: bool) -> str:
    if all_complete or week_index < current_week_index:
        return "Complete"
    if week_index == current_week_index:
        return "Current"
    return "Upcoming"


def core_progress(items: Iterable[StudyPlanItem]) -> tuple[int, int]:
    core = [i for i in items if i.priority == PRIORITY_CORE]
    return sum(1 for i in core if i.status == ITEM_COMPLETE), len(core)


def deadline_fits(plan: StudyPlan, forecast_week: int) -> bool:
    if plan.mode != MODE_FIXED or plan.deadline is None:
        return True
    return sched.week_end_date(to_sched_plan(plan), forecast_week) <= plan.deadline


def blocking_dependencies(graph: PlanGraph, item: StudyPlanItem) -> list[StudyPlanItem]:
    """Hard prerequisites of `item` that are not complete yet."""
    prereq_ids = {
        d.prerequisite_item_id
        for d in graph.dependencies
        if d.dependent_item_id == item.id and d.kind == DEP_HARD
    }
    return [
        graph.item_by_id[pid]
        for pid in prereq_ids
        if pid in graph.item_by_id and graph.item_by_id[pid].status != ITEM_COMPLETE
    ]
