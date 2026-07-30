"""Run the guide importer against real guides and report what the gate says.

Live Anthropic calls — the counterpart to `effort_sweep.py`. Used to verify that
the importer produces structure the validator accepts, that the recomputed
arithmetic matches, that source offsets resolve against the verbatim guide, and
that subject eligibility behaves in both directions.

    uv run python -m scripts.import_sweep ../docs/CURRICULUM.md --weeks 12
    uv run python -m scripts.import_sweep fixtures/anatomy_guide.md --weeks 10

Nothing here writes to the database.
"""

import argparse
import asyncio
import json
import sys
import time
from datetime import date, timedelta
from pathlib import Path

from app.services import llm
from app.services.study_plan_import import validate_import, validate_request


async def run(path: Path, weeks: int, capacity: int, mode: str, deadline: date | None) -> int:
    guide = path.read_text()
    start = date(2026, 7, 27)

    errors = validate_request(
        guide_text=guide,
        requested_weeks=weeks,
        weekly_capacity_minutes=capacity,
        mode=mode,
        deadline=deadline,
        start_date=start,
    )
    if errors:
        print("request rejected before any call:")
        for error in errors:
            print(f"  - {error}")
        return 2

    counted = await llm._client().messages.count_tokens(
        model="claude-opus-5",
        system=[{"type": "text", "text": llm.IMPORT_RUBRIC}],
        messages=[{"role": "user", "content": guide}],
    )
    print(f"guide: {path}  ({len(guide)} chars, {counted.input_tokens} input tokens)")

    started = time.monotonic()
    raw = await llm.import_guide(
        guide_text=guide,
        requested_weeks=weeks,
        weekly_capacity_minutes=capacity,
        mode=mode,
        deadline=deadline.isoformat() if deadline else None,
        subject_hint="",
        title_hint="",
    )
    print(f"imported in {time.monotonic() - started:.1f}s\n")

    result = validate_import(
        raw,
        guide_text=guide,
        requested_weeks=weeks,
        weekly_capacity_minutes=capacity,
        mode=mode,
        deadline=deadline,
        start_date=start,
    )
    preview = result.preview

    print(f"subject           {preview['subject']}  [{preview['subject_slug']}]")
    print(f"recall cards      {preview['supports_recall_cards']}")
    print(f"title             {preview['title']}")
    print(f"phases / weeks    {len(preview['phases'])} / {len(preview['weeks'])}")
    print(f"items             {len(preview['items'])}")
    print(f"total             {preview['total_minutes']} min")
    print(f"dependencies      {len(preview['dependencies'])}\n")

    print("checks")
    for check in result.checks:
        arrow = " →" if check.destination else ""
        print(f"  {check.status:12} {check.label:22} {check.value}{arrow}")
        if check.detail:
            print(f"               {check.detail}")
    print(f"\ncan_create: {result.can_create}\n")

    print("concise titles")
    for phase in preview["phases"]:
        owned = [w for w in preview["weeks"] if w["phase_index"] == phase["index"]]
        print(f"  {phase['index']}. {phase['overview_title']:24} ({phase['full_title']})")
        for week in owned:
            print(f"      w{week['index']:<3} {week['overview_title']:22} {week['full_title']}")

    bad = [i["key"] for i in preview["items"] if not i["source_offsets_ok"]]
    print(f"\nsource offsets: {len(preview['items']) - len(bad)} of "
          f"{len(preview['items'])} resolve against the guide")

    loads: dict[int, int] = {}
    for item in preview["items"]:
        loads[item["week_index"]] = loads.get(item["week_index"], 0) + item["estimate_minutes"]
    print("weekly load (recomputed, not the model's arithmetic)")
    for index in sorted(loads):
        flag = "  OVER" if loads[index] > capacity else ""
        print(f"  week {index:<3} {loads[index]:>5} / {capacity} min{flag}")

    Path("/tmp/import_sweep_last.json").write_text(json.dumps(raw, indent=2))
    return 0 if result.can_create else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("guide", type=Path)
    parser.add_argument("--weeks", type=int, default=12)
    parser.add_argument("--capacity", type=int, default=720)
    parser.add_argument("--mode", choices=["flexible", "fixed"], default="flexible")
    parser.add_argument("--deadline", type=date.fromisoformat, default=None)
    args = parser.parse_args()

    deadline = args.deadline
    if args.mode == "fixed" and deadline is None:
        deadline = date(2026, 7, 27) + timedelta(weeks=args.weeks)
    return asyncio.run(run(args.guide, args.weeks, args.capacity, args.mode, deadline))


if __name__ == "__main__":
    sys.exit(main())
