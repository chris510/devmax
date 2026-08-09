#!/usr/bin/env python
"""Compare scoring quality and token spend across ``effort`` levels.

This is a live, paid evaluation tool rather than a test. Independent cases run
with bounded concurrency, results print in source-file order, and every result
owns its own input/output/cache usage even when calls overlap.

    uv run python scripts/effort_sweep.py scripts/effort_cases.json --dry-run
    uv run python scripts/effort_sweep.py cases.json --max-cost-usd 0.20
    uv run python scripts/effort_sweep.py cases.json --levels low medium --dry-run
"""

from __future__ import annotations

import argparse
import asyncio
import statistics
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import get_settings  # noqa: E402
from app.services import llm  # noqa: E402
from scripts.effort_sweep_support import (  # noqa: E402
    JsonlRecorder,
    PreparedCall,
    Usage,
    UsageTap,
    actual_cost,
    add_paid_evaluation_args,
    capture_usage,
    case_key,
    count_prepared_calls,
    enforce_budget,
    estimate_cost,
    hydrate_grounding,
    levels_for,
    load_cases,
    load_result_records,
    make_result_record,
    output_path_for,
    prepare_call,
    print_preflight,
    rate_for_model,
    run_bounded,
    select_cases,
    usage_from_record,
)


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
    resumed: bool = False


def _expected_axes(case: dict) -> tuple[int | None, int | None, int | None]:
    return (
        case.get("expected_accuracy"),
        case.get("expected_depth"),
        case.get("expected_boundaries"),
    )


def build_completion(case: dict[str, Any], *, model: str, effort: str | None) -> dict:
    return llm.build_score_answer_completion(
        model=model,
        effort=effort,
        topic=case["topic"],
        mastery_summary=case.get("mastery_summary", ""),
        question_asked=case["question"],
        answer_text=case["answer"],
        follow_up_question=case.get("follow_up_question"),
        follow_up_answer=case.get("follow_up_answer", ""),
        follow_up_used=True,
        answer_basis=case.get("answer_basis", ""),
        answer_rubric=case.get("answer_rubric"),
    )


def prepare_cases(
    cases: list[dict], *, levels: list[str | None], model: str
) -> list[PreparedCall]:
    return [
        prepare_call(
            index=index,
            case=case,
            kind="scoring",
            effort=level,
            completion=build_completion(case, model=model, effort=level),
        )
        for level in levels
        for index, case in enumerate(cases)
    ]


def result_payload(result: Result) -> dict[str, Any]:
    return {
        "expected_score": result.expected_score,
        "score": result.score,
        "expected_axes": list(result.expected_axes),
        "axes": list(result.axes),
        "feedback": result.feedback,
    }


def result_from_record(prepared: PreparedCall, record: dict[str, Any]) -> Result:
    try:
        payload = record["result"]
        return Result(
            index=prepared.index,
            case=prepared.case_name,
            expected_score=payload["expected_score"],
            score=int(payload["score"]),
            expected_axes=tuple(payload["expected_axes"]),
            axes=tuple(int(value) for value in payload["axes"]),
            usage=usage_from_record(record),
            feedback=str(payload.get("feedback", "")),
            resumed=True,
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"invalid saved scoring result for {prepared.case_name}") from exc


async def run_case(
    prepared: PreparedCall,
    tap: UsageTap,
    recorder: JsonlRecorder,
    *,
    model: str,
) -> tuple[Result, dict[str, Any]]:
    case = prepared.case
    key = f"{prepared.effort}:{prepared.index}:{prepared.fingerprint[:8]}"
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
    scored = Result(
        index=prepared.index,
        case=prepared.case_name,
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
    record = make_result_record(
        prepared, model=model, result=result_payload(scored), usage=scored.usage
    )
    recorder.append(record)
    return scored, record


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


def level_label(level: str | None) -> str:
    return level or "none"


def print_summary(by_level: dict[str, list[Result]]) -> None:
    print("\n=== summary ===")
    print(
        f"  {'level':<10} {'input':>8} {'cache-r':>8} {'cache-w':>8} "
        f"{'output':>8} {'mean dev':>9} {'exact':>8}"
    )
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
            f"  {level:<10} {sum(r.usage.input_tokens for r in results):>8} "
            f"{sum(r.usage.cache_read_tokens for r in results):>8} "
            f"{sum(r.usage.cache_write_tokens for r in results):>8} "
            f"{sum(r.usage.output_tokens for r in results):>8} "
            f"{mean_dev:>9} {exact:>8}"
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
    add_paid_evaluation_args(parser)
    parser.add_argument(
        "--grounding-manifest",
        type=Path,
        help="approved cards manifest that owns each case's question, basis, and rubric",
    )
    parser.add_argument("--verbose", action="store_true", help="print feedback per case")
    args = parser.parse_args()
    if args.concurrency < 1:
        parser.error("--concurrency must be at least 1")

    cases = select_cases(
        load_cases(args.cases, parser),
        names=args.case_names,
        tags=args.tags,
        parser=parser,
    )
    if args.grounding_manifest:
        cases = hydrate_grounding(cases, args.grounding_manifest, parser)

    sys.stdout.reconfigure(line_buffering=True)
    settings = get_settings()
    levels = levels_for(args.levels, settings.scoring_effort)
    prepared = prepare_cases(cases, levels=levels, model=settings.scoring_model)

    try:
        prior_by_fingerprint = load_result_records(args.resume, kind="scoring")
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    reusable = {} if args.fresh else prior_by_fingerprint
    resumed_calls = [call for call in prepared if call.fingerprint in reusable]
    pending_calls = [call for call in prepared if call.fingerprint not in reusable]
    try:
        resumed_results = {
            call.fingerprint: result_from_record(call, reusable[call.fingerprint])
            for call in resumed_calls
        }
        rate = rate_for_model(
            settings.scoring_model,
            input_override=args.input_price_per_million,
            output_override=args.output_price_per_million,
        )
    except ValueError as exc:
        parser.error(str(exc))

    if pending_calls and not settings.anthropic_api_key:
        print(
            "ANTHROPIC_API_KEY is unset — token preflight and paid calls need a key.",
            file=sys.stderr,
        )
        return 1

    input_counts = (
        await count_prepared_calls(
            pending_calls, concurrency=args.concurrency
        )
        if pending_calls
        else {}
    )
    estimate = estimate_cost(
        pending_calls,
        input_counts=input_counts,
        prior_records=list(prior_by_fingerprint.values()),
        fallback_output_tokens=512,
        rate=rate,
    )
    print_preflight(
        estimate,
        selected=len(prepared),
        resumed=len(resumed_calls),
        rate=rate,
    )
    enforce_budget(
        estimate,
        budget=args.max_cost_usd,
        dry_run=args.dry_run,
        parser=parser,
    )
    if args.dry_run:
        print("  dry run complete — no paid Message calls were made")
        return 0

    output_path = output_path_for(
        requested=args.output,
        resume=args.resume,
        kind="scoring",
        parser=parser,
    )

    original_effort = settings.scoring_effort
    by_level: dict[str, list[Result]] = {}
    new_records: list[dict[str, Any]] = []
    try:
        with JsonlRecorder(output_path) as recorder, capture_usage() as tap:
            for level in levels:
                settings.scoring_effort = level
                label = level_label(level)
                print(
                    f"\n=== effort={label} (model={settings.scoring_model}, "
                    f"concurrency={args.concurrency}) ==="
                )
                level_calls = [call for call in prepared if call.effort == level]
                level_pending = [
                    call for call in level_calls if call.fingerprint not in resumed_results
                ]
                for call in level_calls:
                    if call.fingerprint in resumed_results:
                        recorder.append(reusable[call.fingerprint])

                outcomes = await run_bounded(
                    level_pending,
                    args.concurrency,
                    lambda _index, call: run_case(
                        call, tap, recorder, model=settings.scoring_model
                    ),
                )
                new_by_fingerprint = {
                    call.fingerprint: outcome[0]
                    for call, outcome in zip(level_pending, outcomes, strict=True)
                }
                new_records.extend(outcome[1] for outcome in outcomes)
                results = [
                    resumed_results.get(call.fingerprint)
                    or new_by_fingerprint[call.fingerprint]
                    for call in level_calls
                ]
                by_level[label] = results
                for result in results:
                    expected = "—" if result.expected_score is None else str(result.expected_score)
                    flag = ""
                    if result.expected_score is not None and result.score != result.expected_score:
                        flag = f"  <-- off by {abs(result.score - result.expected_score)}"
                    print(
                        f"  {result.case[:40]:<40} score={result.score} expected={expected} "
                        f"axes={result.axes} in={result.usage.input_tokens:>5} "
                        f"out={result.usage.output_tokens:>5}"
                        f"{'  [resumed]' if result.resumed else ''}{flag}"
                    )
                    if args.verbose:
                        print(f"      {result.feedback}")
    finally:
        settings.scoring_effort = original_effort

    print_summary(by_level)
    print(f"\nnew paid-call cost: ${actual_cost(new_records, rate):.4f}")
    print(f"results: {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
