#!/usr/bin/env python
"""Run the dark Recall-only contract against Claude without changing runtime.

The command is deliberately fail-closed: paid Messages require approved human
labels, exact Anthropic input-token counts, and an explicit cost ceiling. A
credential-free dry run uses a local byte ceiling and never initializes a
provider client.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
import uuid
from decimal import Decimal
from functools import lru_cache
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from anthropic import AsyncAnthropic  # noqa: E402
from pydantic_settings import BaseSettings, SettingsConfigDict  # noqa: E402

from app.services import llm  # noqa: E402
from app.services.scoring_provider import ProviderCallTrace  # noqa: E402
from scripts import effort_sweep, v2_recall_eval  # noqa: E402
from scripts.effort_sweep_support import (  # noqa: E402
    INPUT_COUNT_ANTHROPIC_EXACT,
    JsonlRecorder,
    ModelRate,
    PreparedCall,
    RecordedEvaluationFailure,
    Usage,
    UsageTap,
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
    make_failure_record,
    make_result_record,
    make_run_manifest,
    output_path_for,
    positive_decimal,
    prepare_call,
    print_preflight,
    rate_for_model,
    run_bounded_collect,
    select_cases,
)

KIND = v2_recall_eval.KIND
LOCAL_INPUT_FRAMING_ALLOWANCE = 2048
FALLBACK_OUTPUT_TOKENS = llm.SCORING_OUTPUT_TOKEN_LIMIT


class EvalSettings(BaseSettings):
    """Load only evaluator inputs, never unrelated app/deployment secrets."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    anthropic_api_key: str = ""
    scoring_model: str = "claude-sonnet-5"
    scoring_effort: str | None = "low"


@lru_cache
def get_settings() -> EvalSettings:
    return EvalSettings()


def prepare_cases(
    cases: list[dict[str, Any]],
    *,
    levels: list[str | None],
    model: str,
) -> list[PreparedCall]:
    """Prepare the same V2 builder payload used by production scoring."""
    return [
        prepare_call(
            index=index,
            case=case,
            kind=KIND,
            effort=level,
            completion=v2_recall_eval.build_completion(
                case,
                model=model,
                effort=level,
            ),
        )
        for level in levels
        for index, case in enumerate(cases)
    ]


def conservative_input_bound(call: PreparedCall) -> int:
    """Return a credential-free ceiling for visible request bytes plus framing."""
    counted = llm.count_params_for_completion(call.completion)
    visible_bytes = len(
        json.dumps(
            counted,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    )
    return visible_bytes + LOCAL_INPUT_FRAMING_ALLOWANCE


def claude_actual_cost(
    records: list[dict[str, Any]],
    *,
    input_per_million: Decimal,
    output_per_million: Decimal,
    cache_read_per_million: Decimal,
    cache_write_per_million: Decimal,
) -> Decimal:
    """Price Anthropic's mutually exclusive input/cache usage buckets."""
    totals = {
        field: sum(int(record["usage"].get(field, 0)) for record in records)
        for field in (
            "input_tokens",
            "output_tokens",
            "cache_read_tokens",
            "cache_write_tokens",
        )
    }
    return (
        Decimal(totals["input_tokens"]) * input_per_million
        + Decimal(totals["output_tokens"]) * output_per_million
        + Decimal(totals["cache_read_tokens"]) * cache_read_per_million
        + Decimal(totals["cache_write_tokens"]) * cache_write_per_million
    ) / Decimal(1_000_000)


async def run_case(
    prepared: PreparedCall,
    tap: UsageTap,
    recorder: JsonlRecorder,
    client: AsyncAnthropic,
    stage2_pack_fingerprint: str,
    evaluation_run_id: str,
) -> tuple[v2_recall_eval.Result, dict[str, Any]]:
    """Make one non-retried call and flush exactly one typed evidence row."""
    key = f"{prepared.effort}:{prepared.index}:{prepared.fingerprint[:8]}"
    tap.start(key)
    token = case_key.set(key)
    started = time.monotonic()
    call_traces: list[ProviderCallTrace] = []
    data: dict[str, Any] | None = None
    result: v2_recall_eval.Result | None = None
    error: BaseException | None = None
    try:
        # build_score_v2_completion owns retry=False; do not add a sweep retry.
        data = await llm._complete(
            **prepared.completion,
            client_override=client,
            call_traces=call_traces,
        )
    except Exception as exc:
        error = exc
    finally:
        case_key.reset(token)
    elapsed_ms = int((time.monotonic() - started) * 1000)
    tapped_usage = tap.usage_for(key)
    trace = call_traces[-1] if call_traces else None
    usage = (
        Usage(
            input_tokens=trace.input_tokens,
            output_tokens=trace.output_tokens,
            cache_read_tokens=trace.cached_input_tokens,
            cache_write_tokens=trace.cache_write_tokens,
        )
        if trace is not None
        else tapped_usage
    )
    if error is None:
        try:
            assert data is not None
            result = v2_recall_eval.parse_result(prepared, data, usage)
        except Exception as exc:
            error = exc

    common = {
        "qualification_fingerprint": v2_recall_eval.deployment_fingerprint(
            prepared.completion
        ),
        "stage2_pack_fingerprint": stage2_pack_fingerprint,
        "evaluation_run_id": evaluation_run_id,
        "fresh": True,
        "provider_elapsed_ms": trace.latency_ms if trace is not None else elapsed_ms,
    }
    if trace is not None and trace.response_id:
        common["provider_response_id"] = trace.response_id
    if trace is not None and trace.response_model:
        common["provider_response_model"] = trace.response_model

    if error is not None:
        record = make_failure_record(
            prepared,
            model=str(prepared.completion["model"]),
            failure=v2_recall_eval.failure_payload(
                prepared,
                error,
                failure_type=(
                    trace.outcome
                    if trace is not None and trace.outcome != "success"
                    else "v2_contract_error"
                    if isinstance(error, llm.LLMError)
                    else None
                ),
            ),
            usage=usage,
        )
        record.update(common)
        record["evidence_outcome"] = "failure"
        recorder.append(record)
        raise RecordedEvaluationFailure(error, record) from error

    assert data is not None
    assert result is not None
    record = make_result_record(
        prepared,
        model=str(prepared.completion["model"]),
        result=v2_recall_eval.result_payload(result),
        usage=result.usage,
    )
    record.update(common)
    record["evidence_outcome"] = "success"
    recorder.append(record)
    return result, record


def print_results(
    label: str,
    results: list[v2_recall_eval.Result],
    *,
    verbose: bool,
) -> None:
    print(f"\n=== Claude V2 Recall effort={label} ===")
    for result in results:
        decisions_match = (
            v2_recall_eval.behavioral_decisions(result.flow, result.recall)
            == v2_recall_eval.behavioral_decisions(
                result.expected_flow, result.expected_recall
            )
        )
        flag = "" if decisions_match else "  <-- product decision"
        print(
            f"  {result.case[:40]:<40} recall={result.recall} "
            f"expected={result.expected_recall} flow={result.flow} "
            f"expected-flow={result.expected_flow} "
            f"in={result.usage.input_tokens:>5} out={result.usage.output_tokens:>5}"
            f"{'  [resumed]' if result.resumed else ''}{flag}"
        )
        if verbose:
            detail = result.follow_up_question or result.feedback
            if detail:
                print(f"      {detail}")


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("cases", type=Path, help="JSON file containing V2 Recall cases")
    add_paid_evaluation_args(parser)
    parser.add_argument(
        "--cache-read-price-per-million",
        type=positive_decimal,
        help="explicit Anthropic cache-read USD per million tokens",
    )
    parser.add_argument(
        "--cache-write-price-per-million",
        type=positive_decimal,
        help="explicit Anthropic cache-write USD per million tokens",
    )
    parser.add_argument(
        "--grounding-manifest",
        type=Path,
        help="approved cards manifest that owns question, answer basis, and rubric",
    )
    parser.add_argument(
        "--exact-input-counts",
        action="store_true",
        help=(
            "transmit prepared payloads to Anthropic's non-generating token-count "
            "endpoint during a dry run; without this flag dry runs stay local"
        ),
    )
    parser.add_argument(
        "--model",
        help="Claude model; defaults to the configured production scoring model",
    )
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument(
        "--enforce-v2-recall-gate",
        action="store_true",
        help="exit nonzero on any reviewed Recall or production-decision failure",
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

    pack_failures = v2_recall_eval.stage2_pack_failures(cases)
    stage2_pack_digest = v2_recall_eval.stage2_pack_fingerprint(cases)
    print(f"  Stage 2 pack       {stage2_pack_digest}")
    if pack_failures:
        print(f"  Stage 2 gate       {len(pack_failures)} issue(s)")
        if not args.dry_run:
            parser.error(
                "paid run not started: the complete V2 Recall Stage 2 pack must "
                "be approved, grounded, and cover every required boundary"
            )

    settings = get_settings()
    model = args.model or settings.scoring_model
    levels = levels_for(args.levels, settings.scoring_effort)
    try:
        prepared = prepare_cases(cases, levels=levels, model=model)
        prior_by_fingerprint = load_result_records(args.resume, kind=KIND)
    except (OSError, ValueError) as exc:
        parser.error(str(exc))

    reusable = {} if args.fresh else prior_by_fingerprint
    mismatched_pack = [
        record.get("case", "unknown")
        for record in reusable.values()
        if record.get("stage2_pack_fingerprint") != stage2_pack_digest
    ]
    if mismatched_pack:
        parser.error(
            "saved V2 Recall results use a different or missing Stage 2 pack fingerprint"
        )
    resumed_calls = [call for call in prepared if call.fingerprint in reusable]
    pending_calls = [call for call in prepared if call.fingerprint not in reusable]
    if pending_calls and not args.dry_run:
        if args.input_price_per_million is None:
            parser.error(
                "--input-price-per-million is required for a paid V2 run"
            )
        if args.output_price_per_million is None:
            parser.error(
                "--output-price-per-million is required for a paid V2 run"
            )
        if args.cache_read_price_per_million is None:
            parser.error(
                "--cache-read-price-per-million is required for an exact paid-run cost"
            )
        if args.cache_write_price_per_million is None:
            parser.error(
                "--cache-write-price-per-million is required for an exact paid-run cost"
            )
        if args.qualification_expires_at is None:
            parser.error(
                "--qualification-expires-at is required for a paid V2 run"
            )
    try:
        resumed_results = {
            call.fingerprint: v2_recall_eval.result_from_record(
                call, reusable[call.fingerprint]
            )
            for call in resumed_calls
        }
        rate = rate_for_model(
            model,
            input_override=args.input_price_per_million,
            output_override=args.output_price_per_million,
        )
    except ValueError as exc:
        parser.error(str(exc))

    api_key = str(settings.anthropic_api_key).strip()
    use_provider_count = bool(
        pending_calls and api_key and (args.exact_input_counts or not args.dry_run)
    )
    provider_client = (
        AsyncAnthropic(
            api_key=api_key,
            max_retries=0,
            timeout=llm.SDK_TIMEOUT_SECONDS,
        )
        if use_provider_count
        else None
    )
    if pending_calls and args.exact_input_counts and not api_key:
        parser.error(
            "--exact-input-counts requires ANTHROPIC_API_KEY because it transmits "
            "the prepared evaluation payloads"
        )
    if use_provider_count:
        try:
            input_counts = await count_prepared_calls(
                pending_calls,
                concurrency=args.concurrency,
                client=provider_client,
            )
        except Exception as exc:  # provider/SDK errors are safe preflight failures
            print(f"Anthropic input-token preflight failed: {exc}", file=sys.stderr)
            return 1
        input_label = "counted input"
        input_method = "Anthropic Messages token-count endpoint (no generation)"
    elif pending_calls and not args.dry_run:
        print(
            "ANTHROPIC_API_KEY is unset — paid Claude API calls require a separate key.",
            file=sys.stderr,
        )
        return 1
    else:
        input_counts = {
            call.fingerprint: conservative_input_bound(call) for call in pending_calls
        }
        input_label = "bounded input"
        input_method = "local UTF-8 byte ceiling plus framing allowance"

    cache_rates = (
        args.cache_read_price_per_million,
        args.cache_write_price_per_million,
    )
    priced_cache_rates = [value for value in cache_rates if value is not None]
    budget_rate = (
        ModelRate(
            model=rate.model,
            input_per_million=max(rate.input_per_million, *priced_cache_rates),
            output_per_million=rate.output_per_million,
            label=f"{rate.label}; highest input/cache component",
        )
        if priced_cache_rates
        else rate
    )
    estimate = estimate_cost(
        pending_calls,
        input_counts=input_counts,
        prior_records=[],
        fallback_output_tokens=FALLBACK_OUTPUT_TOKENS,
        rate=budget_rate,
    )
    print_preflight(
        estimate,
        selected=len(prepared),
        resumed=len(resumed_calls),
        rate=budget_rate,
        input_label=input_label,
    )
    print(f"  input method       {input_method}")
    enforce_budget(
        estimate,
        budget=args.max_cost_usd,
        dry_run=args.dry_run,
        parser=parser,
    )
    if args.dry_run:
        if provider_client is not None:
            await provider_client.close()
        print("  dry run complete — no paid Message calls were made")
        return 0

    if pending_calls and provider_client is None:  # guarded by the key check above
        raise AssertionError("paid V2 Recall run has no Anthropic client")

    output_path = output_path_for(
        requested=args.output,
        resume=args.resume,
        kind="claude-v2-recall",
        parser=parser,
    )
    by_level: dict[str, list[v2_recall_eval.Result]] = {}
    new_records: list[dict[str, Any]] = []
    run_failures: list[BaseException] = []
    evaluation_run_id = str(uuid.uuid4())
    with JsonlRecorder(output_path) as recorder, capture_usage() as tap:
        if pending_calls:
            if args.max_cost_usd is None:
                raise AssertionError("paid Claude V2 run has no approved max cost")
            if args.input_price_per_million is None:
                raise AssertionError("paid Claude V2 run has no explicit input rate")
            if args.output_price_per_million is None:
                raise AssertionError("paid Claude V2 run has no explicit output rate")
            if args.cache_read_price_per_million is None:
                raise AssertionError("paid Claude V2 run has no cache-read rate")
            if args.cache_write_price_per_million is None:
                raise AssertionError("paid Claude V2 run has no cache-write rate")
            recorder.append(
                make_run_manifest(
                    kind=KIND,
                    evaluation_run_id=evaluation_run_id,
                    provider="anthropic",
                    model=model,
                    stage2_pack_fingerprint=stage2_pack_digest,
                    calls=pending_calls,
                    qualification_fingerprints={
                        call.fingerprint: v2_recall_eval.deployment_fingerprint(
                            call.completion
                        )
                        for call in pending_calls
                    },
                    approved_max_cost_usd=args.max_cost_usd,
                    rates_per_million_usd={
                        "input": args.input_price_per_million,
                        "output": args.output_price_per_million,
                        "cached_input": args.cache_read_price_per_million,
                        "cache_write": args.cache_write_price_per_million,
                    },
                    input_count_method=INPUT_COUNT_ANTHROPIC_EXACT,
                    input_counts=input_counts,
                    estimate=estimate,
                    qualification_expires_at=args.qualification_expires_at,
                )
            )
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
                    recorder.append(
                        {**reusable[call.fingerprint], "resumed": True}
                    )
            outcomes = await run_bounded_collect(
                level_pending,
                args.concurrency,
                lambda _index, call: run_case(
                    call,
                    tap,
                    recorder,
                    provider_client,  # type: ignore[arg-type]
                    stage2_pack_digest,
                    evaluation_run_id,
                ),
            )
            new_by_fingerprint: dict[str, v2_recall_eval.Result] = {}
            level_failed = False
            for call, outcome in zip(level_pending, outcomes, strict=True):
                if isinstance(outcome, RecordedEvaluationFailure):
                    new_records.append(outcome.record)
                    run_failures.append(outcome.original)
                    level_failed = True
                elif isinstance(outcome, BaseException):
                    run_failures.append(outcome)
                    level_failed = True
                else:
                    new_by_fingerprint[call.fingerprint] = outcome[0]
                    new_records.append(outcome[1])
            if level_failed:
                continue
            results = [
                resumed_results.get(call.fingerprint)
                or new_by_fingerprint[call.fingerprint]
                for call in level_calls
            ]
            by_level[label] = results
            print_results(label, results, verbose=args.verbose)

    if new_records:
        if args.cache_read_price_per_million is None:
            raise AssertionError("paid Claude V2 run has no cache-read rate")
        if args.cache_write_price_per_million is None:
            raise AssertionError("paid Claude V2 run has no cache-write rate")
        paid_cost = claude_actual_cost(
            new_records,
            input_per_million=rate.input_per_million,
            output_per_million=rate.output_per_million,
            cache_read_per_million=args.cache_read_price_per_million,
            cache_write_per_million=args.cache_write_price_per_million,
        )
    else:
        paid_cost = Decimal(0)
    print(f"\nnew paid-call cost: ${paid_cost:.4f}")
    print(f"results: {output_path}")
    if run_failures:
        print(
            f"\nV2 Recall run failed after recording all "
            f"{len(new_records)} paid-call outcome(s):",
            file=sys.stderr,
        )
        for failure in run_failures:
            print(f"  - {type(failure).__name__}: {failure}", file=sys.stderr)
        if provider_client is not None:
            await provider_client.close()
        return 1
    if args.enforce_v2_recall_gate:
        gate_failures = v2_recall_eval.qualification_gate_failures(by_level)
        if gate_failures:
            print("\nV2 Recall gate failed:", file=sys.stderr)
            for failure in gate_failures:
                print(f"  - {failure}", file=sys.stderr)
            return 1
        print("V2 Recall gate passed")
    if provider_client is not None:
        await provider_client.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
