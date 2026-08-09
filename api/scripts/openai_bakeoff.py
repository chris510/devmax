#!/usr/bin/env python
"""Run the reviewed scoring packs against an OpenAI model without changing production.

Examples:
    uv run python scripts/openai_bakeoff.py scoring \
      scripts/grounded_effort_cases_week1.json \
      --grounding-manifest cards.json --tag smoke --dry-run
    uv run python scripts/openai_bakeoff.py reattempt \
      scripts/grounded_reattempt_cases_week1.json \
      --grounding-manifest cards.json --tag smoke --max-cost-usd 0.01
"""

from __future__ import annotations

import argparse
import asyncio
import os
import statistics
import sys
from pathlib import Path
from typing import Any

import httpx
from pydantic_settings import BaseSettings, SettingsConfigDict

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.services import llm  # noqa: E402
from scripts import effort_sweep, reattempt_effort_sweep  # noqa: E402
from scripts.effort_sweep_support import (  # noqa: E402
    JsonlRecorder,
    PreparedCall,
    Usage,
    actual_cost,
    add_paid_evaluation_args,
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
)
from scripts.openai_eval_support import (  # noqa: E402
    REQUEST_TIMEOUT_SECONDS,
    OpenAIEvalError,
    complete,
    conservative_input_bound,
    response_request,
)

DEFAULT_MODEL = "gpt-5.6-luna"
DEFAULT_EFFORT = "low"
DEFAULT_OUTPUT_CAPS = {"scoring": 2048, "reattempt": 512}


class EvalSettings(BaseSettings):
    """Evaluation-only credential loading; no production route consumes this key."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    openai_api_key: str = ""


def positive_int(value: str) -> int:
    parsed = int(value)
    if parsed < 1:
        raise argparse.ArgumentTypeError("must be greater than zero")
    return parsed


def prepare_cases(
    cases: list[dict[str, Any]],
    *,
    kind: str,
    levels: list[str | None],
    model: str,
    max_output_tokens: int,
) -> list[PreparedCall]:
    builder = (
        effort_sweep.build_completion
        if kind == "scoring"
        else reattempt_effort_sweep.build_completion
    )
    prepared: list[PreparedCall] = []
    for level in levels:
        for index, case in enumerate(cases):
            completion = builder(case, model=model, effort=level)
            completion = {
                **completion,
                "provider": "openai-responses",
                "max_tokens": max_output_tokens,
            }
            prepared.append(
                prepare_call(
                    index=index,
                    case=case,
                    kind=kind,
                    effort=level,
                    completion=completion,
                )
            )
    return prepared


def _scoring_result(
    prepared: PreparedCall, data: dict[str, Any], usage: Usage
) -> effort_sweep.Result:
    parsed = llm.parse_score_result(data, follow_up_used=True)
    assert parsed.accuracy is not None
    assert parsed.depth is not None
    assert parsed.boundaries is not None
    return effort_sweep.Result(
        index=prepared.index,
        case=prepared.case_name,
        expected_score=prepared.case.get("expected_score"),
        score=parsed.score if parsed.score is not None else -1,
        expected_axes=effort_sweep._expected_axes(prepared.case),
        axes=(parsed.accuracy, parsed.depth, parsed.boundaries),
        usage=usage,
        feedback=parsed.feedback,
    )


def _reattempt_result(
    prepared: PreparedCall, data: dict[str, Any], usage: Usage
) -> reattempt_effort_sweep.Result:
    parsed = llm.parse_reattempt_result(data)
    return reattempt_effort_sweep.Result(
        index=prepared.index,
        name=prepared.case_name,
        expected=prepared.case["expected_accuracy"],
        actual=parsed.accuracy,
        summary=parsed.mastery_summary,
        usage=usage,
    )


async def run_case(
    prepared: PreparedCall,
    recorder: JsonlRecorder,
    *,
    api_key: str,
    client: httpx.AsyncClient,
) -> tuple[effort_sweep.Result | reattempt_effort_sweep.Result, dict[str, Any]]:
    response = await complete(
        response_request(prepared.completion, kind=prepared.kind),
        api_key=api_key,
        client=client,
    )
    if prepared.kind == "scoring":
        result = _scoring_result(prepared, response.data, response.usage)
        payload = effort_sweep.result_payload(result)
    else:
        result = _reattempt_result(prepared, response.data, response.usage)
        payload = reattempt_effort_sweep.result_payload(result)
    record = make_result_record(
        prepared,
        model=prepared.completion["model"],
        result=payload,
        usage=response.usage,
    )
    record["provider_response_id"] = response.response_id
    record["provider_response_model"] = response.model
    record["provider_elapsed_ms"] = response.elapsed_ms
    recorder.append(record)
    return result, record


def print_scoring_results(
    label: str, results: list[effort_sweep.Result], *, verbose: bool
) -> None:
    print(f"\n=== OpenAI scoring effort={label} ===")
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
        if verbose:
            print(f"      {result.feedback}")


def print_reattempt_results(
    label: str, results: list[reattempt_effort_sweep.Result], *, verbose: bool
) -> None:
    print(f"\n=== OpenAI reattempt effort={label} ===")
    for result in results:
        flag = "" if result.actual == result.expected else "  <-- mismatch"
        print(
            f"  {result.name[:42]:<42} accuracy={result.actual} "
            f"expected={result.expected} out={result.usage.output_tokens:>5}"
            f"{'  [resumed]' if result.resumed else ''}{flag}"
        )
        if verbose:
            print(f"      {result.summary}")
    deviations = [abs(result.actual - result.expected) for result in results]
    false_pass = sum(result.expected <= 2 and result.actual >= 3 for result in results)
    false_fail = sum(result.expected >= 3 and result.actual <= 2 for result in results)
    print(
        f"  exact={sum(result.actual == result.expected for result in results)}/{len(results)} "
        f"within-one={sum(deviation <= 1 for deviation in deviations)}/{len(results)} "
        f"mean-dev={statistics.mean(deviations):.2f} "
        f"false-pass={false_pass} false-fail={false_fail} "
        f"in={sum(result.usage.input_tokens for result in results)} "
        f"cache-r={sum(result.usage.cache_read_tokens for result in results)} "
        f"out={sum(result.usage.output_tokens for result in results)}"
    )


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("kind", choices=("scoring", "reattempt"))
    parser.add_argument("cases", type=Path)
    add_paid_evaluation_args(parser)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--max-output-tokens", type=positive_int)
    parser.add_argument("--verbose", action="store_true")
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

    levels = levels_for(args.levels, DEFAULT_EFFORT)
    max_output_tokens = args.max_output_tokens or DEFAULT_OUTPUT_CAPS[args.kind]
    prepared = prepare_cases(
        cases,
        kind=args.kind,
        levels=levels,
        model=args.model,
        max_output_tokens=max_output_tokens,
    )

    try:
        prior_by_fingerprint = load_result_records(args.resume, kind=args.kind)
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    reusable = {} if args.fresh else prior_by_fingerprint
    resumed_calls = [call for call in prepared if call.fingerprint in reusable]
    pending_calls = [call for call in prepared if call.fingerprint not in reusable]
    result_from_record = (
        effort_sweep.result_from_record
        if args.kind == "scoring"
        else reattempt_effort_sweep.result_from_record
    )
    try:
        resumed_results = {
            call.fingerprint: result_from_record(call, reusable[call.fingerprint])
            for call in resumed_calls
        }
        rate = rate_for_model(
            args.model,
            input_override=args.input_price_per_million,
            output_override=args.output_price_per_million,
        )
    except ValueError as exc:
        parser.error(str(exc))

    requests = {
        call.fingerprint: response_request(call.completion, kind=call.kind)
        for call in pending_calls
    }
    input_counts = {
        fingerprint: conservative_input_bound(request)
        for fingerprint, request in requests.items()
    }
    estimate = estimate_cost(
        pending_calls,
        input_counts=input_counts,
        prior_records=[],
        fallback_output_tokens=max_output_tokens,
        rate=rate,
    )
    print_preflight(
        estimate,
        selected=len(prepared),
        resumed=len(resumed_calls),
        rate=rate,
        input_label="bounded input",
    )
    print("  input method       UTF-8 byte ceiling plus provider-framing allowance")
    enforce_budget(
        estimate,
        budget=args.max_cost_usd,
        dry_run=args.dry_run,
        parser=parser,
    )
    if args.dry_run:
        print("  dry run complete — no paid Responses calls were made")
        return 0

    api_key = (
        os.environ.get("OPENAI_API_KEY", "").strip()
        or EvalSettings().openai_api_key.strip()
    )
    if pending_calls and not api_key:
        print(
            "OPENAI_API_KEY is unset — ChatGPT credits cannot authorize API calls.",
            file=sys.stderr,
        )
        return 1

    output_path = output_path_for(
        requested=args.output,
        resume=args.resume,
        kind=f"openai-{args.kind}",
        parser=parser,
    )
    by_level: dict[str, list[Any]] = {}
    new_records: list[dict[str, Any]] = []
    try:
        with JsonlRecorder(output_path) as recorder:
            async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT_SECONDS) as client:
                for level in levels:
                    label = effort_sweep.level_label(level)
                    level_calls = [call for call in prepared if call.effort == level]
                    level_pending = [
                        call
                        for call in level_calls
                        if call.fingerprint not in resumed_results
                    ]
                    for call in level_calls:
                        if call.fingerprint in resumed_results:
                            recorder.append(reusable[call.fingerprint])
                    outcomes = await run_bounded(
                        level_pending,
                        args.concurrency,
                        lambda _index, call: run_case(
                            call, recorder, api_key=api_key, client=client
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
                    if args.kind == "scoring":
                        print_scoring_results(label, results, verbose=args.verbose)
                    else:
                        print_reattempt_results(label, results, verbose=args.verbose)
    except OpenAIEvalError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    if args.kind == "scoring":
        effort_sweep.print_summary(by_level)
    print(f"\nnew paid-call cost: ${actual_cost(new_records, rate):.4f}")
    print(f"results: {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
