#!/usr/bin/env python
"""Compare coached re-attempt grading across effort levels.

Live and paid. Cases run concurrently but print in file order, with per-call
usage captured by the same context-aware log tap as the scoring sweep.

    uv run python scripts/reattempt_effort_sweep.py scripts/reattempt_effort_cases.json --dry-run
    uv run python scripts/reattempt_effort_sweep.py cases.json --max-cost-usd 0.08
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
    name: str
    expected: int
    actual: int
    summary: str
    usage: Usage
    resumed: bool = False


def build_completion(case: dict[str, Any], *, model: str, effort: str | None) -> dict:
    return llm.build_reattempt_completion(
        model=model,
        effort=effort,
        topic=case["topic"],
        question_asked=case["question"],
        feedback_given=case["feedback"],
        reattempt_answer=case["reattempt_answer"],
        unaided_accuracy=case["unaided_accuracy"],
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
            kind="reattempt",
            effort=level,
            completion=build_completion(case, model=model, effort=level),
        )
        for level in levels
        for index, case in enumerate(cases)
    ]


def result_payload(result: Result) -> dict[str, Any]:
    return {
        "expected": result.expected,
        "actual": result.actual,
        "summary": result.summary,
    }


def result_from_record(prepared: PreparedCall, record: dict[str, Any]) -> Result:
    try:
        payload = record["result"]
        return Result(
            index=prepared.index,
            name=prepared.case_name,
            expected=int(payload["expected"]),
            actual=int(payload["actual"]),
            summary=str(payload["summary"]),
            usage=usage_from_record(record),
            resumed=True,
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"invalid saved re-attempt result for {prepared.case_name}") from exc


async def run_case(
    prepared: PreparedCall,
    tap: UsageTap,
    recorder: JsonlRecorder,
    *,
    model: str,
) -> tuple[Result, dict[str, Any]]:
    case = prepared.case
    key = f"reattempt:{prepared.effort}:{prepared.index}:{prepared.fingerprint[:8]}"
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
    scored = Result(
        index=prepared.index,
        name=prepared.case_name,
        expected=case["expected_accuracy"],
        actual=result.accuracy,
        summary=result.mastery_summary,
        usage=tap.usage_for(key),
    )
    record = make_result_record(
        prepared, model=model, result=result_payload(scored), usage=scored.usage
    )
    recorder.append(record)
    return scored, record


def level_label(level: str | None) -> str:
    return level or "none"


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("cases", type=Path)
    add_paid_evaluation_args(parser)
    parser.add_argument("--verbose", action="store_true", help="print mastery summaries")
    parser.add_argument(
        "--grounding-manifest",
        type=Path,
        help="approved cards manifest that owns each case's question, basis, and rubric",
    )
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
    settings = get_settings()
    levels = levels_for(args.levels, settings.reattempt_effort)
    prepared = prepare_cases(cases, levels=levels, model=settings.reattempt_model)

    try:
        prior_by_fingerprint = load_result_records(args.resume, kind="reattempt")
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
            settings.reattempt_model,
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
        fallback_output_tokens=128,
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
        kind="reattempt",
        parser=parser,
    )

    original_effort = settings.reattempt_effort
    new_records: list[dict[str, Any]] = []
    try:
        with JsonlRecorder(output_path) as recorder, capture_usage() as tap:
            for level in levels:
                settings.reattempt_effort = level
                label = level_label(level)
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
                        call, tap, recorder, model=settings.reattempt_model
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
                print(f"\n=== reattempt effort={label} ===")
                for result in results:
                    flag = "" if result.actual == result.expected else "  <-- mismatch"
                    print(
                        f"  {result.name[:42]:<42} accuracy={result.actual} "
                        f"expected={result.expected} out={result.usage.output_tokens:>5}"
                        f"{'  [resumed]' if result.resumed else ''}{flag}"
                    )
                    if args.verbose:
                        print(f"      {result.summary}")
                deviations = [abs(r.actual - r.expected) for r in results]
                false_pass = sum(
                    r.expected <= 2 and r.actual >= 3 for r in results
                )
                false_fail = sum(
                    r.expected >= 3 and r.actual <= 2 for r in results
                )
                print(
                    f"  exact={sum(r.actual == r.expected for r in results)}/{len(results)} "
                    f"within-one={sum(d <= 1 for d in deviations)}/{len(results)} "
                    f"mean-dev={statistics.mean(deviations):.2f} "
                    f"false-pass={false_pass} false-fail={false_fail} "
                    f"in={sum(r.usage.input_tokens for r in results)} "
                    f"cache-r={sum(r.usage.cache_read_tokens for r in results)} "
                    f"cache-w={sum(r.usage.cache_write_tokens for r in results)} "
                    f"out={sum(r.usage.output_tokens for r in results)}"
                )
    finally:
        settings.reattempt_effort = original_effort
    print(f"\nnew paid-call cost: ${actual_cost(new_records, rate):.4f}")
    print(f"results: {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
