#!/usr/bin/env python
"""Compare scoring quality and token spend across ``effort`` levels.

This is a live, paid evaluation tool rather than a test. Independent cases run
with bounded concurrency, results print in source-file order, and every result
owns its own input/output/cache usage even when calls overlap.

    uv run python scripts/effort_sweep.py scripts/effort_cases.json
    uv run python scripts/effort_sweep.py cases.json --levels low medium --concurrency 4
"""

from __future__ import annotations

import argparse
import asyncio
import statistics
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

DEFAULT_LEVELS = ("low", "medium")


@dataclass
class Result:
    index: int
    case: str
    expected_score: int | None
    score: int
    expected_axes: tuple[int | None, int | None, int | None]
    axes: tuple[int, int, int]
    usage: Usage
    feedback: str = ""


def _expected_axes(case: dict) -> tuple[int | None, int | None, int | None]:
    return (
        case.get("expected_accuracy"),
        case.get("expected_depth"),
        case.get("expected_boundaries"),
    )


async def run_case(index: int, case: dict, level: str, tap: UsageTap) -> Result:
    key = f"{level}:{index}"
    tap.start(key)
    token = case_key.set(key)
    try:
        result = await llm.score_answer(
            topic=case["topic"],
            mastery_summary=case.get("mastery_summary", ""),
            question_asked=case["question"],
            answer_text=case["answer"],
            follow_up_question=case.get("follow_up_question"),
            follow_up_answer=case.get("follow_up_answer", ""),
            # Every case must return a comparable final grade. Follow-up-band
            # agreement is derived from the axes/composite below.
            follow_up_used=True,
            answer_basis=case.get("answer_basis", ""),
            answer_rubric=case.get("answer_rubric"),
        )
    finally:
        case_key.reset(token)

    assert result.accuracy is not None
    assert result.depth is not None
    assert result.boundaries is not None
    return Result(
        index=index,
        case=case.get("name", case["topic"]),
        expected_score=case.get("expected_score"),
        score=result.score if result.score is not None else -1,
        expected_axes=_expected_axes(case),
        axes=(
            result.accuracy,
            result.depth,
            result.boundaries,
        ),
        usage=tap.usage_for(key),
        feedback=result.feedback,
    )


def _axis_metrics(results: list[Result], index: int) -> tuple[int, int, int]:
    pairs = [
        (result.axes[index], result.expected_axes[index])
        for result in results
        if result.expected_axes[index] is not None
    ]
    exact = sum(actual == expected for actual, expected in pairs)
    within_one = sum(abs(actual - expected) <= 1 for actual, expected in pairs)
    return exact, within_one, len(pairs)


def _accuracy_errors(results: list[Result]) -> tuple[int, int]:
    labeled = [r for r in results if r.expected_axes[0] is not None]
    false_pass = sum(r.expected_axes[0] <= 2 and r.axes[0] >= 3 for r in labeled)
    false_fail = sum(r.expected_axes[0] >= 3 and r.axes[0] <= 2 for r in labeled)
    return false_pass, false_fail


def print_summary(by_level: dict[str, list[Result]]) -> None:
    print("\n=== summary ===")
    print(f"  {'level':<10} {'in tok':>9} {'out tok':>9} {'mean dev':>9} {'exact':>8}")
    for level, results in by_level.items():
        deviations = [
            abs(r.score - r.expected_score)
            for r in results
            if r.expected_score is not None
        ]
        mean_dev = f"{statistics.mean(deviations):.2f}" if deviations else "—"
        exact = (
            f"{sum(d == 0 for d in deviations)}/{len(deviations)}" if deviations else "—"
        )
        print(
            f"  {level:<10} {sum(r.usage.input_tokens for r in results):>9} "
            f"{sum(r.usage.output_tokens for r in results):>9} {mean_dev:>9} {exact:>8}"
        )

        false_pass, false_fail = _accuracy_errors(results)
        print(f"    accuracy false pass={false_pass} false fail={false_fail}")
        for name, index in (("accuracy", 0), ("depth", 1), ("boundaries", 2)):
            axis_exact, within_one, total = _axis_metrics(results, index)
            if total:
                print(
                    f"    {name:<13} exact={axis_exact}/{total} "
                    f"within-one={within_one}/{total}"
                )


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("cases", type=Path, help="JSON file: a list of case objects")
    parser.add_argument("--levels", nargs="+", default=list(DEFAULT_LEVELS))
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument(
        "--grounding-manifest",
        type=Path,
        help="approved cards manifest that owns each case's question, basis, and rubric",
    )
    parser.add_argument("--verbose", action="store_true", help="print feedback per case")
    args = parser.parse_args()
    if args.concurrency < 1:
        parser.error("--concurrency must be at least 1")

    cases = load_cases(args.cases, parser)
    if args.grounding_manifest:
        cases = hydrate_grounding(cases, args.grounding_manifest, parser)

    sys.stdout.reconfigure(line_buffering=True)
    settings = get_settings()
    if not settings.anthropic_api_key:
        print("ANTHROPIC_API_KEY is unset — this script makes live calls.", file=sys.stderr)
        return 1

    original_effort = settings.scoring_effort
    by_level: dict[str, list[Result]] = {}
    try:
        with capture_usage() as tap:
            for level in args.levels:
                settings.scoring_effort = level
                print(
                    f"\n=== effort={level} (model={settings.scoring_model}, "
                    f"concurrency={args.concurrency}) ==="
                )
                results = await run_bounded(
                    cases, args.concurrency,
                    lambda index, case, effort=level: run_case(index, case, effort, tap),
                )
                by_level[level] = results
                for result in results:
                    expected = "—" if result.expected_score is None else str(result.expected_score)
                    flag = ""
                    if result.expected_score is not None and result.score != result.expected_score:
                        flag = f"  <-- off by {abs(result.score - result.expected_score)}"
                    print(
                        f"  {result.case[:40]:<40} score={result.score} expected={expected} "
                        f"axes={result.axes} in={result.usage.input_tokens:>5} "
                        f"out={result.usage.output_tokens:>5}{flag}"
                    )
                    if args.verbose:
                        print(f"      {result.feedback}")
    finally:
        settings.scoring_effort = original_effort

    print_summary(by_level)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
