"""Create a deterministic first-party Study Plan without an LLM call.

This is deliberately separate from ``app.seed``. Card activation controls the
SM-2 review queue; this command creates the phases, weeks, and work items that
power the Study Plan timeline. Neither path infers or mutates the other.

    uv run python -m app.seed_study_plan --activate --start-date 2026-07-27

The committed manifest is passed through the same validation gate as a pasted
guide. A stable ``seed_key`` recorded on the creation revision makes reruns
idempotent without adding curriculum identity to the user-facing plan schema.
"""

import argparse
import asyncio
import hashlib
import json
from collections.abc import AsyncIterator, Mapping, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.db import session_factory
from app.models import (
    CONFIDENCE_HIGH,
    DEP_IMPORTED,
    FOUNDER_USER_ID,
    ITEM_COMPLETE,
    ITEM_PENDING,
    ITEM_REMOVED,
    PLAN_ACTIVE,
    PLAN_ARCHIVED,
    PLAN_COMPLETED,
    PLAN_PAUSED,
    REVISION_CREATED,
    StudyPlan,
    StudyPlanItem,
    StudyPlanItemDependency,
    StudyPlanPhase,
    StudyPlanRevision,
)
from app.routers.deps import get_settings_row, now_in
from app.services import study_plan as sp
from app.services import study_plan_import as spi

DEFAULT_MANIFEST = Path(__file__).resolve().parent.parent / "plans" / "senior-backend-12-week.json"


class PlanSeedError(ValueError):
    """The committed manifest or requested activation is unsafe to apply."""


@dataclass(frozen=True)
class SeedResult:
    plan_id: str
    title: str
    created: bool
    updated: bool
    active: bool
    weeks: int
    items: int
    version: int


@dataclass(frozen=True)
class ExistingSeed:
    plan: StudyPlan
    creation_revision: StudyPlanRevision
    current_revision: StudyPlanRevision
    version: int


# Reviewed legacy identities. Production was created from the 72-item v2
# manifest at 0715cce; v3 later added one optional item per week. Neither schema
# persisted manifest keys, but both persisted global guide order and titles.
# The title fingerprints make the positional backfill fail closed for any other
# 72- or 84-row plan rather than assigning curriculum identity by count alone.
V2_ITEM_KEYS = (
    "W1-L1", "W1-L2", "W1-P1", "W1-P2", "W1-P3", "W1-R1",
    "W2-L1", "W2-L2", "W2-P1", "W2-P2", "W2-P3", "W2-R1",
    "W3-L1", "W3-L2", "W3-P1", "W3-P2", "W3-P3", "W3-R1",
    "W4-L1", "W4-P1", "W4-P2", "W4-P3", "W4-P4", "W4-R1",
    "W5-L1", "W5-L2", "W5-P1", "W5-P2", "W5-P3", "W5-R1",
    "W6-L1", "W6-L2", "W6-P1", "W6-P2", "W6-P3", "W6-R1",
    "W7-L1", "W7-L2", "W7-P1", "W7-P2", "W7-P3", "W7-R1",
    "W8-L1", "W8-L2", "W8-P1", "W8-P2", "W8-P3", "W8-R1",
    "W9-L1", "W9-L2", "W9-P1", "W9-P2", "W9-P3", "W9-R1",
    "W10-P1", "W10-P2", "W10-P3", "W10-L1", "W10-P4", "W10-R1",
    "W11-P1", "W11-P2", "W11-P3", "W11-L1", "W11-P4", "W11-R1",
    "W12-P1", "W12-P2", "W12-P3", "W12-L1", "W12-P4", "W12-R1",
)
V2_ITEM_TITLES = (
    "Complete the system design delivery framework lesson",
    "Turn functional and non-functional requirements into design constraints",
    "Practice two pointers, sliding windows, and intervals",
    "Deliver one requirements-to-deep-dives system design",
    "Review the design and harvest specific mechanism gaps",
    "Reconstruct the delivery framework closed-book",
    "Trace request paths and network failure handling",
    "Study API mechanics, authentication, and authorization boundaries",
    "Practice stacks, linked lists, and binary search",
    "Model entities and access patterns for one backend design",
    "Review failure paths and harvest mechanism gaps",
    "Reconstruct the request path and trust boundaries closed-book",
    "Study caching, invalidation, and cache failure modes",
    "Study sharding, consistent hashing, replication, and CAP",
    "Practice heaps, depth-first search, and breadth-first search",
    "Design a partitioned and replicated storage path",
    "Review consistency decisions and harvest mechanism gaps",
    "Reconstruct partitioning and consistency trade-offs closed-book",
    "Study read scaling, write scaling, replica lag, fan-out, backpressure, and queues",
    "Complete a read-heavy full system design",
    "Complete a write-heavy full system design",
    "Practice graphs, topological ordering, and shortest paths",
    "Start the behavioral story catalog with scope and ownership",
    "Harvest and reconstruct scaling gaps",
    "Study contention, distributed locks, and overload containment",
    "Study sagas, durable workflows, circuit breakers, and exactly-once effects",
    "Complete a workflow-heavy full system design",
    "Complete a failure-heavy full system design",
    "Practice backtracking, dynamic programming, and greedy algorithms; "
    "add ambiguity and perseverance stories",
    "Harvest and reconstruct resilience gaps",
    "Study realtime updates, large blobs, and long-running jobs",
    "Study transactional outboxes and recovery after interrupted delivery",
    "Complete a realtime full system design",
    "Complete a platform-control-plane full system design",
    "Practice tries, prefix sums, and matrix problems; complete the behavioral story catalog",
    "Harvest and reconstruct platform mechanism gaps",
    "Study PostgreSQL and Redis mechanics and failure modes",
    "Study DynamoDB and Cassandra data modeling trade-offs",
    "Complete three timed coding sessions",
    "Choose and defend storage technologies in one full design",
    "Practice the Big Three behavioral answers",
    "Reconstruct database selection trade-offs closed-book",
    "Study Kafka, API gateway, and asynchronous delivery mechanics",
    "Study Elasticsearch, blob storage, CDN, rate limiting, and time-series behavior",
    "Complete three timed coding sessions",
    "Design a searchable media or document delivery system",
    "Practice the Big Three behavioral answers with follow-ups",
    "Reconstruct messaging and search trade-offs closed-book",
    "Study Flink, ZooKeeper, and coordination guarantees",
    "Study approximate structures, proximity search, and vector indexes",
    "Complete three timed coding sessions",
    "Design a streaming or proximity-search system",
    "Practice senior-scope behavioral follow-ups",
    "Reconstruct coordination and index trade-offs closed-book",
    "Complete two full system design mocks",
    "Complete three coding mocks",
    "Complete two behavioral practices with follow-ups",
    "Compare mock performance with the senior readiness gates",
    "Repair the highest-impact observed gap",
    "Reconstruct the repaired mechanism closed-book",
    "Complete two target-shaped system design mocks",
    "Complete three coding mocks including a plain-editor session",
    "Complete two behavioral practices with senior-scope follow-ups",
    "Review evidence for the active target-company loop",
    "Repair the highest-impact observed gap",
    "Reconstruct the repaired mechanism closed-book",
    "Complete two final full system design mocks",
    "Complete three final coding mocks",
    "Complete two final behavioral practices",
    "Evaluate external performance against every readiness gate",
    "Write the remaining-gap plan for scheduled interviews",
    "Reconstruct the final repaired mechanism closed-book",
)
V3_ITEM_KEYS = (
    "W1-L1", "W1-L2", "W1-P1", "W1-O1", "W1-P2", "W1-P3", "W1-R1",
    "W2-L1", "W2-L2", "W2-P1", "W2-O1", "W2-P2", "W2-P3", "W2-R1",
    "W3-L1", "W3-L2", "W3-P1", "W3-O1", "W3-P2", "W3-P3", "W3-R1",
    "W4-L1", "W4-P1", "W4-P2", "W4-P3", "W4-O1", "W4-P4", "W4-R1",
    "W5-L1", "W5-L2", "W5-P1", "W5-P2", "W5-P3", "W5-O1", "W5-R1",
    "W6-L1", "W6-L2", "W6-P1", "W6-P2", "W6-P3", "W6-O1", "W6-R1",
    "W7-L1", "W7-L2", "W7-P1", "W7-O1", "W7-P2", "W7-P3", "W7-R1",
    "W8-L1", "W8-L2", "W8-P1", "W8-O1", "W8-P2", "W8-P3", "W8-R1",
    "W9-L1", "W9-L2", "W9-P1", "W9-O1", "W9-P2", "W9-P3", "W9-R1",
    "W10-P1", "W10-P2", "W10-O1", "W10-P3", "W10-L1", "W10-P4", "W10-R1",
    "W11-P1", "W11-P2", "W11-O1", "W11-P3", "W11-L1", "W11-P4", "W11-R1",
    "W12-P1", "W12-P2", "W12-O1", "W12-P3", "W12-L1", "W12-P4", "W12-R1",
)
V2_TITLE_SHA256 = "13d28456be5290879b7dd7697ec16d5b3436a4d5772a8cd1d5a53306ea524d0b"
V3_TITLE_SHA256 = "a5975886bcbda2ee7bc656627e1acd1b93419f8ba19565155acac07bbf20dca3"

REVISION_CURRICULUM = "curriculum_update"


@asynccontextmanager
async def _session(db: AsyncSession | None) -> AsyncIterator[AsyncSession]:
    if db is not None:
        yield db
    else:
        async with session_factory() as owned:
            yield owned


def _read_bundle(path: Path) -> tuple[dict[str, Any], str]:
    manifest = json.loads(path.read_text())
    guide_text = str(manifest["guide_text"])
    actual_hash = hashlib.sha256(guide_text.encode()).hexdigest()
    expected_hash = str(manifest.get("guide_sha256", ""))
    if actual_hash != expected_hash:
        raise PlanSeedError(
            "the embedded curriculum guide changed without a reviewed manifest update "
            f"(expected {expected_hash}, got {actual_hash})"
        )

    # Railway builds the API directory as its whole image context, so the
    # repository-level source guide is intentionally not a runtime dependency.
    # When it is present (development and CI), pin it too: an authoritative
    # curriculum edit must force an explicit manifest review.
    source_path_value = manifest.get("source_guide_path")
    if source_path_value:
        source_path = (path.parent / str(source_path_value)).resolve()
        if source_path.exists():
            source_hash = hashlib.sha256(source_path.read_bytes()).hexdigest()
            expected_source_hash = str(manifest.get("source_guide_sha256", ""))
            if source_hash != expected_source_hash:
                raise PlanSeedError(
                    "the source curriculum changed without a reviewed plan-manifest "
                    f"update (expected {expected_source_hash}, got {source_hash})"
                )
    return manifest, guide_text


def _raw_import(manifest: Mapping[str, Any]) -> dict[str, Any]:
    """Expand the compact curated manifest into the importer wire shape."""
    why_defaults = {
        "learn": "Builds the mechanism knowledge needed for later design decisions.",
        "practice": "Turns the curriculum into observable interview performance.",
        "retrieve": "Tests whether the week's mechanisms can be reconstructed without notes.",
    }
    done_defaults = {
        "learn": "Explain the mechanisms and their trade-offs closed-book.",
        "practice": "Complete the work under the stated interview constraints and review it.",
        "retrieve": "Reconstruct the key decisions closed-book and record any gaps.",
    }
    raw_weeks = []
    raw_items = []
    order = 0
    for week in manifest["weeks"]:
        raw_weeks.append(
            {
                "index": week["index"],
                "phase_index": week["phase_index"],
                "full_title": week["full_title"],
                "overview_title": week["overview_title"],
            }
        )
        for item in week["items"]:
            order += 1
            raw_items.append(
                {
                    "key": item["key"],
                    "week_index": week["index"],
                    "guide_order": order,
                    "curriculum_key": item["key"],
                    "type": item["type"],
                    "priority": item["priority"],
                    "full_title": item["title"],
                    "why_it_matters": item.get("why", why_defaults[item["type"]]),
                    "done_when": item.get("done_when", done_defaults[item["type"]]),
                    "estimate_minutes": item["minutes"],
                    "estimate_source": "imported",
                    "estimate_confidence": "high",
                    "origin": "imported",
                    "source_item_key": item.get("source_item_key"),
                    # The whole committed guide remains the provenance. Empty
                    # spans are honest here: these activities combine several
                    # bullets rather than pretending one sentence owns them.
                    "source_start": None,
                    "source_end": None,
                    "source_excerpt": "",
                    "recall_supported": False,
                    "parser_interpretation": "Curated first-party curriculum activity.",
                    "resources": item.get("resources", []),
                    "mapped_recall_topics": item.get("mapped_recall_topics", []),
                    "stretch_actions": item.get("stretch_actions", []),
                }
            )

    return {
        "subject": manifest["subject"],
        "subject_slug": manifest["subject_slug"],
        "supports_technical_recall_cards": manifest["supports_technical_recall_cards"],
        "plan_title": manifest["title"],
        "phases": manifest["phases"],
        "weeks": raw_weeks,
        "items": raw_items,
        "dependencies": manifest.get("dependencies", []),
        "unresolved_estimates": [],
        "possible_omissions": [],
    }


def validate_bundle(path: Path, *, start_date: date) -> tuple[dict[str, Any], spi.ImportResult]:
    manifest, guide_text = _read_bundle(path)
    result = spi.validate_import(
        _raw_import(manifest),
        guide_text=guide_text,
        requested_weeks=int(manifest["requested_weeks"]),
        weekly_capacity_minutes=int(manifest["weekly_capacity_minutes"]),
        mode=str(manifest["mode"]),
        deadline=None,
        start_date=start_date,
    )
    failures = [f"{check.key}: {check.value}" for check in result.checks if check.status != "ok"]
    if failures:
        raise PlanSeedError("the first-party plan did not pass its gate: " + "; ".join(failures))
    return manifest, result


async def _existing_seed(
    db: AsyncSession, seed_keys: Sequence[str]
) -> ExistingSeed | None:
    revisions = (
        await db.exec(
            select(StudyPlanRevision)
            .join(StudyPlan, StudyPlan.id == StudyPlanRevision.plan_id)
            .where(StudyPlan.user_id == FOUNDER_USER_ID)
        )
    ).all()
    accepted_keys = set(seed_keys)
    matching = [
        row for row in revisions if (row.after or {}).get("seed_key") in accepted_keys
    ]
    creations = [row for row in matching if row.kind == REVISION_CREATED]
    if not creations:
        return None
    if len(creations) > 1:
        raise PlanSeedError(
            "more than one founder plan claims a canonical or legacy curriculum seed key"
        )
    creation = creations[0]
    matching = [row for row in matching if row.plan_id == creation.plan_id]
    plan = (
        await db.exec(
            select(StudyPlan).where(
                StudyPlan.user_id == FOUNDER_USER_ID,
                col(StudyPlan.id) == creation.plan_id,
            )
        )
    ).first()
    if plan is None:
        return None
    versioned: list[tuple[int, StudyPlanRevision]] = []
    for row in matching:
        try:
            versioned.append((int((row.after or {}).get("seed_version", 0)), row))
        except (TypeError, ValueError):
            continue
    version, current = max(
        versioned,
        key=lambda entry: (entry[0], entry[1].created_at),
        default=(0, creation),
    )
    return ExistingSeed(
        plan=plan,
        creation_revision=creation,
        current_revision=current,
        version=version,
    )


def _backfill_legacy_keys(items: list[StudyPlanItem]) -> None:
    if any(item.curriculum_key for item in items):
        raise PlanSeedError(
            "a legacy curriculum unexpectedly already has stable item keys; nothing was updated"
        )
    ordered = sorted(items, key=lambda item: item.guide_order)
    orders = [item.guide_order for item in ordered]
    shapes = {
        len(V2_ITEM_KEYS): (V2_ITEM_KEYS, V2_TITLE_SHA256),
        len(V3_ITEM_KEYS): (V3_ITEM_KEYS, V3_TITLE_SHA256),
    }
    shape = shapes.get(len(ordered))
    expected_orders = list(range(1, len(ordered) + 1))
    title_sha256 = hashlib.sha256(
        "".join(f"{item.full_title}\n" for item in ordered).encode()
    ).hexdigest()
    if shape is None or orders != expected_orders or title_sha256 != shape[1]:
        raise PlanSeedError(
            "the existing legacy curriculum is not the reviewed 72-item v2 or "
            "84-item v3 plan; "
            "nothing was updated"
        )
    for item, key in zip(ordered, shape[0], strict=True):
        item.curriculum_key = key


def _apply_item_content(
    item: StudyPlanItem,
    entry: Mapping[str, Any],
    *,
    phase_id,
    week_id,
) -> None:
    item.phase_id = phase_id
    item.week_id = week_id
    item.guide_order = int(entry["guide_order"])
    item.curriculum_key = str(entry["curriculum_key"])
    item.type = str(entry["type"])
    item.priority = str(entry["priority"])
    item.full_title = str(entry["full_title"])
    item.why_it_matters = str(entry["why_it_matters"])
    item.done_when = str(entry["done_when"])
    item.estimate_minutes = int(entry["estimate_minutes"])
    item.estimate_source = str(entry["estimate_source"])
    item.estimate_confidence = str(entry["estimate_confidence"])
    item.origin = str(entry["origin"])
    item.source_start = entry["source_start"]
    item.source_end = entry["source_end"]
    item.source_excerpt = str(entry["source_excerpt"])
    item.recall_supported = bool(entry["recall_supported"])
    item.parser_interpretation = str(entry["parser_interpretation"])
    item.resources = list(entry["resources"])
    item.mapped_recall_topics = list(entry["mapped_recall_topics"])
    item.stretch_actions = list(entry["stretch_actions"])
    item.updated_at = datetime.now(UTC)


def _legacy_pristine_violations(graph: sp.PlanGraph) -> list[str]:
    """State that cannot be assigned honestly to v4's new item lineage."""
    violations: list[str] = []
    if graph.plan.current_week_index != 1:
        violations.append("the plan has advanced beyond Week 1")
    if graph.plan.revision != 1:
        violations.append("the plan has saved revisions")
    if graph.plan.status in (PLAN_COMPLETED, PLAN_ARCHIVED):
        violations.append("the plan is completed or archived")
    if any(item.status != ITEM_PENDING for item in graph.items):
        violations.append("an item is complete, deferred, or removed")
    if any(item.notes for item in graph.items):
        violations.append("an item has notes")
    if any(
        item.study_block_label
        or item.study_block_weekday is not None
        or item.study_block_minute_of_day is not None
        or item.study_block_reminder_on
        for item in graph.items
    ):
        violations.append("an item has a saved study block or reminder")
    if any(
        week.override_capacity_minutes is not None or week.advanced_at is not None
        for week in graph.weeks
    ):
        violations.append("a week has an override or advancement history")
    return violations


async def _upgrade_existing_plan(
    db: AsyncSession,
    existing: ExistingSeed,
    manifest: Mapping[str, Any],
    validated: spi.ImportResult,
    *,
    start_date: date,
) -> SeedResult:
    """Apply a reviewed manifest version in place without touching review state."""
    plan = existing.plan
    graph = await sp.load_plan_graph(db, plan)
    legacy_upgrade = existing.version <= 3
    if legacy_upgrade:
        violations = _legacy_pristine_violations(graph)
        if violations:
            raise PlanSeedError(
                "the deployed legacy curriculum is not pristine; refusing an in-place "
                "upgrade because v4 is a new schedule, not the same item lineage "
                f"({'; '.join(violations)}); nothing was updated"
            )
        _backfill_legacy_keys(graph.items)

    phases = {phase.index: phase for phase in graph.phases}
    weeks = {week.index: week for week in graph.weeks}
    preview = validated.preview
    target_phase_indexes = {int(entry["index"]) for entry in preview["phases"]}
    target_week_indexes = {int(entry["index"]) for entry in preview["weeks"]}
    if target_phase_indexes != set(phases) or target_week_indexes != set(weeks):
        raise PlanSeedError(
            "a curriculum upgrade may not add or remove phases or weeks in place"
        )

    for entry in preview["phases"]:
        phase = phases[int(entry["index"])]
        phase.full_title = str(entry["full_title"])
        phase.overview_title = str(entry["overview_title"])
        phase.description = str(entry["description"])
        db.add(phase)

    phase_by_week: dict[int, StudyPlanPhase] = {}
    for entry in preview["weeks"]:
        week = weeks[int(entry["index"])]
        phase = phases[int(entry["phase_index"])]
        week.phase_id = phase.id
        week.full_title = str(entry["full_title"])
        week.overview_title = str(entry["overview_title"])
        week.updated_at = datetime.now(UTC)
        phase_by_week[week.index] = phase
        db.add(week)

    existing_by_key = {
        item.curriculum_key: item for item in graph.items if item.curriculum_key is not None
    }
    target_by_key: dict[str, Mapping[str, Any]] = {}
    for entry in preview["items"]:
        key = str(entry.get("curriculum_key") or "").strip()
        if not key or key in target_by_key:
            raise PlanSeedError("the new curriculum has a missing or duplicate stable item key")
        target_by_key[key] = entry

    item_by_key: dict[str, StudyPlanItem] = {}
    for key, entry in target_by_key.items():
        week = weeks[int(entry["week_index"])]
        phase = phase_by_week[week.index]
        item = existing_by_key.get(key)
        if item is None:
            item = StudyPlanItem(
                plan_id=plan.id,
                phase_id=phase.id,
                week_id=week.id,
                guide_order=int(entry["guide_order"]),
                curriculum_key=key,
                type=str(entry["type"]),
                priority=str(entry["priority"]),
                full_title=str(entry["full_title"]),
                estimate_minutes=int(entry["estimate_minutes"]),
            )
        if item.status == ITEM_COMPLETE:
            # A completed row records work the user actually did. A curriculum
            # release may attach safer navigation and recall mappings to the
            # same stable concept, but must not move or redefine that history.
            item.resources = list(entry["resources"])
            item.mapped_recall_topics = list(entry["mapped_recall_topics"])
            item.stretch_actions = list(entry["stretch_actions"])
        else:
            _apply_item_content(item, entry, phase_id=phase.id, week_id=week.id)
        db.add(item)
        item_by_key[key] = item

    # Removing a curriculum activity never deletes its debrief, proposals, or
    # links. A completed historical row remains complete; an unfinished one is
    # retained as removed and no longer participates in capacity.
    for key, item in existing_by_key.items():
        if key not in target_by_key and item.status != ITEM_COMPLETE:
            item.status = ITEM_REMOVED
            item.updated_at = datetime.now(UTC)
            db.add(item)

    await db.flush()
    for key, entry in target_by_key.items():
        if item_by_key[key].status == ITEM_COMPLETE:
            continue
        source_key = entry.get("source_item_key")
        item_by_key[key].source_item_id = (
            item_by_key[str(source_key)].id
            if source_key and str(source_key) in item_by_key
            else None
        )
        db.add(item_by_key[key])

    for dependency in graph.dependencies:
        if dependency.source == DEP_IMPORTED:
            await db.delete(dependency)
    await db.flush()
    now = datetime.now(UTC)
    for entry in preview["dependencies"]:
        prereq = item_by_key.get(str(entry["prerequisite_key"]))
        dependent = item_by_key.get(str(entry["dependent_key"]))
        if prereq is None or dependent is None:
            continue
        auto_confirmed = (
            entry["source"] == DEP_IMPORTED and entry["confidence"] == CONFIDENCE_HIGH
        )
        db.add(
            StudyPlanItemDependency(
                plan_id=plan.id,
                prerequisite_item_id=prereq.id,
                dependent_item_id=dependent.id,
                kind=str(entry["kind"]),
                source=str(entry["source"]),
                confidence=str(entry["confidence"]),
                rationale=str(entry["rationale"]),
                source_excerpt=str(entry["source_excerpt"]),
                confirmed_at=now if auto_confirmed else None,
            )
        )

    base_revision = plan.revision
    previous_start_date = plan.start_date
    plan.title = str(preview["title"])
    plan.subject = str(preview["subject"])
    plan.subject_slug = str(preview["subject_slug"])
    plan.guide_text = str(preview["guide_text"])
    plan.default_weekly_capacity_minutes = int(preview["weekly_capacity_minutes"])
    if legacy_upgrade:
        plan.start_date = start_date
    plan.forecast_end_plan_week = max(target_week_indexes)
    plan.revision += 1
    plan.updated_at = now
    db.add(plan)
    db.add(
        StudyPlanRevision(
            plan_id=plan.id,
            kind=REVISION_CURRICULUM,
            base_plan_revision=base_revision,
            before={
                "seed_key": (existing.current_revision.after or {}).get("seed_key"),
                "seed_version": existing.version,
                "start_date": previous_start_date.isoformat(),
            },
            after={
                "seed_key": manifest["seed_key"],
                "seed_version": int(manifest["version"]),
                "from_version": existing.version,
                "start_date": plan.start_date.isoformat(),
                "weeks": len(preview["weeks"]),
                "items": len(preview["items"]),
            },
            summary=f"Curriculum updated to version {manifest['version']}",
        )
    )
    await db.commit()
    return SeedResult(
        plan_id=str(plan.id),
        title=plan.title,
        created=False,
        updated=True,
        active=plan.status == PLAN_ACTIVE,
        weeks=len(preview["weeks"]),
        items=len(preview["items"]),
        version=int(manifest["version"]),
    )


async def load_first_party_plan(
    path: Path = DEFAULT_MANIFEST,
    *,
    start_date: date | None = None,
    activate: bool = False,
    db: AsyncSession | None = None,
) -> SeedResult:
    """Create or return the founder's curated first-party plan.

    Public users build plans through authenticated imports. This operator path
    is founder-only and therefore never relies on request-local identity.
    """
    async with _session(db) as session:
        if start_date is None:
            settings = await get_settings_row(session, FOUNDER_USER_ID)
            begin = spi.default_start_date(now_in(settings.timezone).date())
        else:
            begin = start_date
        if begin.weekday() != 0:
            raise PlanSeedError(
                f"Study Plan start dates must be Mondays; {begin.isoformat()} is a "
                f"{begin.strftime('%A')}"
            )
        manifest, validated = validate_bundle(path, start_date=begin)
        seed_key = str(manifest["seed_key"])
        raw_legacy_keys = manifest.get("legacy_seed_keys", [])
        if not isinstance(raw_legacy_keys, list) or not raw_legacy_keys:
            raise PlanSeedError("legacy_seed_keys must be a nonempty list")
        if any(not isinstance(key, str) or not key.strip() for key in raw_legacy_keys):
            raise PlanSeedError("legacy_seed_keys must contain only nonempty strings")
        legacy_seed_keys = tuple(key.strip() for key in raw_legacy_keys)
        if seed_key in legacy_seed_keys or len(set(legacy_seed_keys)) != len(
            legacy_seed_keys
        ):
            raise PlanSeedError(
                "legacy_seed_keys must be unique and must exclude the canonical seed key"
            )
        seed_keys = (seed_key, *legacy_seed_keys)

        existing = await _existing_seed(session, seed_keys)
        if existing is not None:
            requested_version = int(manifest["version"])
            if existing.version > requested_version:
                raise PlanSeedError(
                    f"refusing to downgrade curriculum version {existing.version} "
                    f"to {requested_version}"
                )
            if existing.version < requested_version:
                try:
                    return await _upgrade_existing_plan(
                        session,
                        existing,
                        manifest,
                        validated,
                        start_date=begin,
                    )
                except Exception:
                    await session.rollback()
                    raise
            plan = existing.plan
            revision = existing.current_revision
            return SeedResult(
                plan_id=str(plan.id),
                title=plan.title,
                created=False,
                updated=False,
                active=plan.status == PLAN_ACTIVE,
                weeks=int((revision.after or {}).get("weeks", 0)),
                items=int((revision.after or {}).get("items", 0)),
                version=existing.version,
            )

        if activate and await sp.active_plan(session, FOUNDER_USER_ID) is not None:
            raise PlanSeedError(
                "another Study Plan is already active; pause it in the app before "
                "activating this first-party plan"
            )

        rows = spi.build_plan_rows(validated.preview, start_date=begin)
        plan: StudyPlan = rows["plan"]
        plan.user_id = FOUNDER_USER_ID
        plan.status = PLAN_ACTIVE if activate else PLAN_PAUSED
        if not activate:
            plan.paused_at = datetime.now(UTC)

        await sp.insert_plan_rows(session, plan, rows)
        session.add(
            StudyPlanRevision(
                plan_id=plan.id,
                kind=REVISION_CREATED,
                base_plan_revision=1,
                before={},
                after={
                    "seed_key": seed_key,
                    "seed_version": manifest["version"],
                    "weeks": len(rows["weeks"]),
                    "items": len(rows["items"]),
                },
                summary=(
                    f"Plan created from the first-party curriculum · {len(rows['items'])} items"
                ),
            )
        )
        await session.commit()
        return SeedResult(
            plan_id=str(plan.id),
            title=plan.title,
            created=True,
            updated=False,
            active=activate,
            weeks=len(rows["weeks"]),
            items=len(rows["items"]),
            version=int(manifest["version"]),
        )


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed a first-party Unprompted Study Plan")
    parser.add_argument(
        "--file",
        type=Path,
        default=DEFAULT_MANIFEST,
        help=f"curated plan manifest (default: {DEFAULT_MANIFEST})",
    )
    parser.add_argument(
        "--start-date",
        type=date.fromisoformat,
        help="Monday that Week 1 begins, YYYY-MM-DD (default: Monday of this week)",
    )
    parser.add_argument(
        "--activate",
        action="store_true",
        help="make the plan active; refuses if a different plan is already active",
    )
    args = parser.parse_args()

    try:
        result = asyncio.run(
            load_first_party_plan(args.file, start_date=args.start_date, activate=args.activate)
        )
    except PlanSeedError as exc:
        parser.error(str(exc))

    verb = "created" if result.created else ("updated" if result.updated else "already exists")
    state = "active" if result.active else "paused"
    print(
        f"{verb}: {result.title} · {result.weeks} weeks · "
        f"{result.items} items · v{result.version} · {state} · {result.plan_id}"
    )


if __name__ == "__main__":
    main()
