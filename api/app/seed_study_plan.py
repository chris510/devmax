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
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.db import session_factory
from app.models import (
    FOUNDER_USER_ID,
    PLAN_ACTIVE,
    PLAN_PAUSED,
    REVISION_CREATED,
    StudyPlan,
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
    active: bool
    weeks: int
    items: int


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
    db: AsyncSession, seed_key: str
) -> tuple[StudyPlan, StudyPlanRevision] | None:
    revisions = (
        await db.exec(
            select(StudyPlanRevision)
            .join(StudyPlan, StudyPlan.id == StudyPlanRevision.plan_id)
            .where(
                StudyPlan.user_id == FOUNDER_USER_ID,
                col(StudyPlanRevision.kind) == REVISION_CREATED,
            )
        )
    ).all()
    revision = next(
        (row for row in revisions if (row.after or {}).get("seed_key") == seed_key),
        None,
    )
    if revision is None:
        return None
    plan = (
        await db.exec(
            select(StudyPlan).where(
                StudyPlan.user_id == FOUNDER_USER_ID,
                col(StudyPlan.id) == revision.plan_id,
            )
        )
    ).first()
    return (plan, revision) if plan is not None else None


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

        existing = await _existing_seed(session, seed_key)
        if existing is not None:
            plan, revision = existing
            return SeedResult(
                plan_id=str(plan.id),
                title=plan.title,
                created=False,
                active=plan.status == PLAN_ACTIVE,
                weeks=int((revision.after or {}).get("weeks", 0)),
                items=int((revision.after or {}).get("items", 0)),
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
            active=activate,
            weeks=len(rows["weeks"]),
            items=len(rows["items"]),
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

    verb = "created" if result.created else "already exists"
    state = "active" if result.active else "paused"
    print(
        f"{verb}: {result.title} · {result.weeks} weeks · "
        f"{result.items} items · {state} · {result.plan_id}"
    )


if __name__ == "__main__":
    main()
