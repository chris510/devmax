#!/usr/bin/env python
"""Compare coached re-attempt grading across effort levels.

Live and paid. Cases run concurrently but print in file order, with per-call
usage captured by the same context-aware log tap as the scoring sweep.

    uv run python scripts/reattempt_effort_sweep.py scripts/reattempt_effort_cases.json
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import get_settings  # noqa: E402
from app.services import llm  # noqa: E402
from scripts.effort_sweep_support import (  # noqa: E402
    Usage,
    UsageTap,
    capture_usage,
    case_key,
    hydrate_grounding,
    load_cases,
    run_bounded,
)


@dataclass
class Result:
    index: int
    name: str
    expected: int
    actual: int
    summary: str
    usage: Usage


async def run_case(index: int, case: dict, level: str, tap: UsageTap) -> Result:
    key = f"reattempt:{level}:{index}"
    tap.start(key)
    token = case_key.set(key)
    try:
        result = await llm.score_reattempt(
            topic=case["topic"],
            question_asked=case["question"],
            feedback_given=case["feedback"],
            reattempt_answer=case["reattempt_answer"],
            unaided_accuracy=case["unaided_accuracy"],
            answer_basis=case.get("answer_basis", ""),
            answer_rubric=case.get("answer_rubric"),
        )
    finally:
        case_key.reset(token)
    return Result(
        index=index,
        name=case.get("name", case["topic"]),
        expected=case["expected_accuracy"],
        actual=result.accuracy,
        summary=result.mastery_summary,
        usage=tap.usage_for(key),
    )


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("cases", type=Path)
    parser.add_argument("--levels", nargs="+", default=["low", "medium"])
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument(
        "--grounding-manifest",
        type=Path,
        help="approved cards manifest that owns each case's question, basis, and rubric",
    )
    args = parser.parse_args()
    if args.concurrency < 1:
        parser.error("--concurrency must be at least 1")

    cases = load_cases(args.cases, parser)
    if args.grounding_manifest:
        cases = hydrate_grounding(cases, args.grounding_manifest, parser)
    settings = get_settings()
    if not settings.anthropic_api_key:
        print("ANTHROPIC_API_KEY is unset — this script makes live calls.", file=sys.stderr)
        return 1

    original_effort = settings.reattempt_effort
    try:
        with capture_usage() as tap:
            for level in args.levels:
                settings.reattempt_effort = level
                results = await run_bounded(
                    cases, args.concurrency,
                    lambda index, case, effort=level: run_case(index, case, effort, tap),
                )
                print(f"\n=== reattempt effort={level} ===")
                for result in results:
                    flag = "" if result.actual == result.expected else "  <-- mismatch"
                    print(
                        f"  {result.name[:42]:<42} accuracy={result.actual} "
                        f"expected={result.expected} out={result.usage.output_tokens:>5}{flag}"
                    )
                print(
                    f"  exact={sum(r.actual == r.expected for r in results)}/{len(results)} "
                    f"out={sum(r.usage.output_tokens for r in results)}"
                )
    finally:
        settings.reattempt_effort = original_effort
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
