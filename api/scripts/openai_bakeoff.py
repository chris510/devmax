#!/usr/bin/env python
"""Run the reviewed scoring packs against an OpenAI model without changing production.

Examples:
    uv run python scripts/openai_bakeoff.py scoring \
      scripts/grounded_effort_cases_week1.json \
      --grounding-manifest cards.json --tag smoke --dry-run
    uv run python scripts/openai_bakeoff.py reattempt \
      scripts/grounded_reattempt_cases_week1.json \
      --grounding-manifest cards.json --tag smoke --max-cost-usd 0.01
    uv run python scripts/openai_bakeoff.py evidence \
      scripts/grounded_effort_cases_week1_release.json \
      --grounding-manifest cards.json --tag release-pack --dry-run
"""

from __future__ import annotations

import argparse
import asyncio
import os
import statistics
import sys
import uuid
from pathlib import Path
from typing import Any

import httpx
from pydantic_settings import BaseSettings, SettingsConfigDict

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import OPENAI_V2_LUNA_MODEL  # noqa: E402
from app.services import llm  # noqa: E402
from scripts import (  # noqa: E402
    effort_sweep,
    reattempt_effort_sweep,
    structured_evidence_eval,
    v2_recall_eval,
)
from scripts.effort_sweep_support import (  # noqa: E402
    INPUT_COUNT_LOCAL_BOUND,
    INPUT_COUNT_OPENAI_EXACT,
    INPUT_COUNT_OPENAI_REUSED_TOTAL,
    JsonlRecorder,
    ModelRate,
    PreparedCall,
    RecordedEvaluationFailure,
    Usage,
    add_paid_evaluation_args,
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
    pending_case_reviews,
    positive_decimal,
    prepare_call,
    print_preflight,
    rate_for_model,
    run_bounded,
    run_bounded_collect,
    select_cases,
)
from scripts.effort_sweep_support import (
    actual_cost as generic_actual_cost,
)
from scripts.openai_eval_support import (  # noqa: E402
    REQUEST_TIMEOUT_SECONDS,
    V2_EVAL_SAFETY_IDENTIFIER,
    V2_EVAL_SAFETY_IDENTIFIER_FORMAT_VERSION,
    OpenAIEvalError,
    complete,
    conservative_input_bound,
    count_input_tokens,
    response_request,
)
from scripts.openai_eval_support import (
    actual_cost as openai_actual_cost,
)
from scripts.scoring_prompt_variants import (  # noqa: E402
    PRODUCTION,
    SCORING_PROMPT_VARIANTS,
    apply_scoring_prompt_variant,
)

DEFAULT_MODEL = OPENAI_V2_LUNA_MODEL
DEFAULT_EFFORT = "low"
DEFAULT_OUTPUT_CAPS = {
    "scoring": 2048,
    "v2-recall": 2048,
    "reattempt": 512,
    "evidence": 512,
}


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
    prompt_variant: str = PRODUCTION,
) -> list[PreparedCall]:
    builders = {
        "scoring": effort_sweep.build_completion,
        "v2-recall": v2_recall_eval.build_completion,
        "reattempt": reattempt_effort_sweep.build_completion,
        "evidence": structured_evidence_eval.build_completion,
    }
    builder = builders[kind]
    prepared: list[PreparedCall] = []
    for level in levels:
        for index, case in enumerate(cases):
            completion = builder(case, model=model, effort=level)
            if kind == "scoring":
                completion = apply_scoring_prompt_variant(
                    completion, prompt_variant
                )
            elif prompt_variant != PRODUCTION:
                raise ValueError("scoring prompt variants are available only for scoring")
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
                    scoring_prompt_variant=prompt_variant,
                )
            )
    return prepared


def _scoring_result(
    prepared: PreparedCall, data: dict[str, Any], usage: Usage
) -> effort_sweep.Result:
    # Parsed at the cap so the bakeoff always gets a scored result to compare.
    parsed = llm.parse_score_result(data, probes_used=llm.MAX_SCORED_FOLLOW_UPS)
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


def _v2_recall_result(
    prepared: PreparedCall, data: dict[str, Any], usage: Usage
) -> v2_recall_eval.Result:
    try:
        return v2_recall_eval.parse_result(prepared, data, usage)
    except llm.LLMError as exc:
        raise OpenAIEvalError(
            f"{prepared.case_name}: OpenAI response violated the V2 Recall contract: {exc}",
            failure_type="v2_contract_error",
        ) from exc


def _evidence_result(
    prepared: PreparedCall, data: dict[str, Any], usage: Usage
) -> structured_evidence_eval.Result:
    return structured_evidence_eval.parse_result(
        prepared.case,
        data,
        usage=usage,
        index=prepared.index,
        case_name=prepared.case_name,
    )


async def run_case(
    prepared: PreparedCall,
    recorder: JsonlRecorder,
    *,
    api_key: str,
    client: httpx.AsyncClient,
    stage2_pack_fingerprint: str = "",
    evaluation_run_id: str = "",
) -> tuple[
    effort_sweep.Result
    | v2_recall_eval.Result
    | reattempt_effort_sweep.Result
    | structured_evidence_eval.Result,
    dict[str, Any],
]:
    response = None
    error: BaseException | None = None
    result = None
    payload: dict[str, Any] | None = None
    try:
        response = await complete(
            response_request(prepared.completion, kind=prepared.kind),
            api_key=api_key,
            client=client,
        )
        if prepared.kind == "scoring":
            result = _scoring_result(prepared, response.data, response.usage)
            payload = effort_sweep.result_payload(result)
        elif prepared.kind == "v2-recall":
            result = _v2_recall_result(prepared, response.data, response.usage)
            payload = v2_recall_eval.result_payload(result)
        elif prepared.kind == "reattempt":
            result = _reattempt_result(prepared, response.data, response.usage)
            payload = reattempt_effort_sweep.result_payload(result)
        else:
            result = _evidence_result(prepared, response.data, response.usage)
            payload = structured_evidence_eval.result_payload(result)
    except Exception as exc:
        if prepared.kind != "v2-recall":
            raise
        error = exc

    if prepared.kind == "v2-recall":
        error_metadata = error if isinstance(error, OpenAIEvalError) else None
        usage = (
            response.usage
            if response is not None
            else error_metadata.usage if error_metadata is not None else Usage()
        )
        common: dict[str, Any] = {
            "qualification_fingerprint": (
                v2_recall_eval.deployment_fingerprint(prepared.completion)
            ),
            "stage2_pack_fingerprint": stage2_pack_fingerprint,
            "evaluation_run_id": evaluation_run_id,
            "fresh": True,
            "provider_elapsed_ms": (
                response.elapsed_ms
                if response is not None
                else error_metadata.elapsed_ms if error_metadata is not None else 0
            ),
        }
        response_id = (
            response.response_id
            if response is not None
            else error_metadata.response_id if error_metadata is not None else ""
        )
        response_model = (
            response.model
            if response is not None
            else error_metadata.model if error_metadata is not None else ""
        )
        if response_id:
            common["provider_response_id"] = response_id
        if response_model:
            common["provider_response_model"] = response_model
        if error is not None:
            record = make_failure_record(
                prepared,
                model=prepared.completion["model"],
                failure=v2_recall_eval.failure_payload(prepared, error),
                usage=usage,
            )
            record.update(common)
            record["evidence_outcome"] = "failure"
            recorder.append(record)
            raise RecordedEvaluationFailure(error, record) from error

    assert response is not None
    assert payload is not None
    assert result is not None
    record = make_result_record(
        prepared,
        model=prepared.completion["model"],
        result=payload,
        usage=response.usage,
    )
    record["provider_response_id"] = response.response_id
    record["provider_response_model"] = response.model
    record["provider_elapsed_ms"] = response.elapsed_ms
    if prepared.kind == "v2-recall":
        record.update(common)
        record["evidence_outcome"] = "success"
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


def print_v2_recall_results(
    label: str, results: list[v2_recall_eval.Result], *, verbose: bool
) -> None:
    print(f"\n=== OpenAI V2 Recall effort={label} ===")
    for result in results:
        flag = "" if result.decision == result.expected_decision else "  <-- decision"
        print(
            f"  {result.case[:40]:<40} recall={result.recall} "
            f"expected={result.expected_recall} flow={result.flow} "
            f"expected-flow={result.expected_flow} "
            f"in={result.usage.input_tokens:>5} out={result.usage.output_tokens:>5}"
            f"{'  [resumed]' if result.resumed else ''}{flag}"
        )
        if verbose:
            if result.follow_up_question:
                print(f"      {result.follow_up_question}")
            elif result.feedback:
                print(f"      {result.feedback}")

    deviations = [abs(result.recall - result.expected_recall) for result in results]
    print(
        f"  exact={sum(deviation == 0 for deviation in deviations)}/{len(results)} "
        f"within-one={sum(deviation <= 1 for deviation in deviations)}/{len(results)} "
        f"decision-match="
        f"{sum(result.decision == result.expected_decision for result in results)}/"
        f"{len(results)} in={sum(result.usage.input_tokens for result in results)} "
        f"cache-r={sum(result.usage.cache_read_tokens for result in results)} "
        f"out={sum(result.usage.output_tokens for result in results)}"
    )


def print_evidence_results(
    label: str, results: list[structured_evidence_eval.Result], *, verbose: bool
) -> None:
    print(f"\n=== OpenAI structured evidence effort={label} ===")
    for result in results:
        print(
            f"  {result.case[:40]:<40} "
            f"depth={result.depth.eligible}/{result.expected_depth_eligible} "
            f"boundaries={result.boundaries.eligible}/"
            f"{result.expected_boundaries_eligible} "
            f"in={result.usage.input_tokens:>5} out={result.usage.output_tokens:>5}"
            f"{'  [resumed]' if result.resumed else ''}"
        )
        if verbose:
            print(f"      depth={result.depth}")
            print(f"      boundaries={result.boundaries}")


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "kind", choices=("scoring", "v2-recall", "reattempt", "evidence")
    )
    parser.add_argument("cases", type=Path)
    add_paid_evaluation_args(parser)
    parser.add_argument(
        "--cached-input-price-per-million",
        type=positive_decimal,
        help="explicit OpenAI cached-input USD per million tokens",
    )
    parser.add_argument(
        "--cache-write-price-per-million",
        type=positive_decimal,
        help="explicit OpenAI cache-write USD per million tokens",
    )
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--max-output-tokens", type=positive_int)
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument(
        "--exact-input-counts",
        action="store_true",
        help=(
            "send prepared payloads to OpenAI's non-generating input-token endpoint "
            "instead of using the local byte ceiling"
        ),
    )
    parser.add_argument(
        "--reuse-exact-input-total",
        type=positive_int,
        metavar="TOKENS",
        help=(
            "reuse the exact total from an immediately preceding input-token "
            "dry-run whose selected requests are unchanged; makes no new count calls"
        ),
    )
    parser.add_argument(
        "--enforce-reviewed-gate",
        action="store_true",
        help="exit nonzero after recording if any reviewed scoring gate fails",
    )
    parser.add_argument(
        "--enforce-secondary-bucket-gate",
        action="store_true",
        help="exit nonzero on any reviewed Depth/Boundaries 2/3 bucket crossing",
    )
    parser.add_argument(
        "--enforce-evidence-gate",
        action="store_true",
        help="exit nonzero unless exact learner-span eligibility matches review",
    )
    parser.add_argument(
        "--enforce-v2-recall-gate",
        action="store_true",
        help="exit nonzero on any reviewed Recall, flow, or scheduler-bucket failure",
    )
    parser.add_argument(
        "--scoring-prompt-variant",
        choices=SCORING_PROMPT_VARIANTS,
        default=PRODUCTION,
        help="evaluation-only rubric variant; production is unchanged",
    )
    parser.add_argument(
        "--grounding-manifest",
        type=Path,
        help="approved cards manifest that owns each case's question, basis, and rubric",
    )
    args = parser.parse_args()
    if args.concurrency < 1:
        parser.error("--concurrency must be at least 1")
    if args.enforce_reviewed_gate and args.kind != "scoring":
        parser.error("--enforce-reviewed-gate is available only for scoring")
    if args.enforce_secondary_bucket_gate and args.kind != "scoring":
        parser.error("--enforce-secondary-bucket-gate is available only for scoring")
    if args.enforce_evidence_gate and args.kind != "evidence":
        parser.error("--enforce-evidence-gate is available only for evidence")
    if args.enforce_v2_recall_gate and args.kind != "v2-recall":
        parser.error("--enforce-v2-recall-gate is available only for v2-recall")
    if args.exact_input_counts and args.reuse_exact_input_total is not None:
        parser.error(
            "--exact-input-counts and --reuse-exact-input-total are mutually exclusive"
        )

    cases = select_cases(
        load_cases(args.cases, parser),
        names=args.case_names,
        tags=args.tags,
        parser=parser,
    )
    if args.grounding_manifest:
        cases = hydrate_grounding(cases, args.grounding_manifest, parser)
    stage2_pack_digest = ""
    if args.kind == "v2-recall":
        pack_failures = v2_recall_eval.stage2_pack_failures(cases)
        stage2_pack_digest = v2_recall_eval.stage2_pack_fingerprint(cases)
        print(f"  Stage 2 pack       {stage2_pack_digest}")
        if pack_failures:
            print(f"  Stage 2 gate       {len(pack_failures)} issue(s)")
            if not args.dry_run:
                parser.error(
                    "paid run not started: the complete V2 Recall Stage 2 pack "
                    "must be approved, grounded, and cover every required boundary"
                )
    else:
        pending_reviews = pending_case_reviews(cases)
        if pending_reviews:
            print(
                f"  human review       {len(pending_reviews)} case label(s) still pending"
            )
            if not args.dry_run:
                parser.error(
                    "paid run not started: every explicit review_status must be approved"
                )

    levels = levels_for(args.levels, DEFAULT_EFFORT)
    max_output_tokens = args.max_output_tokens or DEFAULT_OUTPUT_CAPS[args.kind]
    try:
        prepared = prepare_cases(
            cases,
            kind=args.kind,
            levels=levels,
            model=args.model,
            max_output_tokens=max_output_tokens,
            prompt_variant=args.scoring_prompt_variant,
        )
    except ValueError as exc:
        parser.error(str(exc))

    if args.kind == "v2-recall":
        deployment_fingerprints: dict[str, set[str]] = {}
        for call in prepared:
            label = effort_sweep.level_label(call.effort)
            deployment_fingerprints.setdefault(label, set()).add(
                v2_recall_eval.deployment_fingerprint(call.completion)
            )
        for label, fingerprints in deployment_fingerprints.items():
            if len(fingerprints) != 1:
                parser.error(
                    f"V2 Recall {label} requests do not share one deployment fingerprint"
                )
            print(f"  qualification     {label}: {next(iter(fingerprints))}")

    try:
        prior_by_fingerprint = load_result_records(args.resume, kind=args.kind)
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    reusable = {} if args.fresh else prior_by_fingerprint
    if args.kind == "v2-recall":
        mismatched_pack = [
            record.get("case", "unknown")
            for record in reusable.values()
            if record.get("stage2_pack_fingerprint") != stage2_pack_digest
        ]
        if mismatched_pack:
            parser.error(
                "saved V2 Recall results use a different or missing Stage 2 pack "
                "fingerprint"
            )
    resumed_calls = [call for call in prepared if call.fingerprint in reusable]
    pending_calls = [call for call in prepared if call.fingerprint not in reusable]
    if args.kind == "v2-recall" and pending_calls and not args.dry_run:
        if args.reuse_exact_input_total is not None:
            parser.error(
                "--reuse-exact-input-total cannot attest per-invocation counts for "
                "a paid V2 run; use --exact-input-counts or the local bound"
            )
        if args.input_price_per_million is None:
            parser.error(
                "--input-price-per-million is required for a paid V2 run"
            )
        if args.output_price_per_million is None:
            parser.error(
                "--output-price-per-million is required for a paid V2 run"
            )
        if args.cached_input_price_per_million is None:
            parser.error(
                "--cached-input-price-per-million is required for an exact paid-run cost"
            )
        if args.cache_write_price_per_million is None:
            parser.error(
                "--cache-write-price-per-million is required for an exact paid-run cost"
            )
        if args.qualification_expires_at is None:
            parser.error(
                "--qualification-expires-at is required for a paid V2 run"
            )
    if args.reuse_exact_input_total is not None and resumed_calls:
        parser.error(
            "--reuse-exact-input-total requires a fresh run with every selected call pending"
        )
    result_readers = {
        "scoring": effort_sweep.result_from_record,
        "v2-recall": v2_recall_eval.result_from_record,
        "reattempt": reattempt_effort_sweep.result_from_record,
        "evidence": structured_evidence_eval.result_from_record,
    }
    result_from_record = result_readers[args.kind]
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
    api_key = (
        os.environ.get("OPENAI_API_KEY", "").strip()
        or EvalSettings().openai_api_key.strip()
    )
    if args.exact_input_counts and pending_calls and not api_key:
        print(
            "OPENAI_API_KEY is unset — exact input counts transmit prepared payloads to OpenAI.",
            file=sys.stderr,
        )
        return 1
    if args.exact_input_counts:
        try:
            async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT_SECONDS) as client:
                counts = await run_bounded(
                    pending_calls,
                    args.concurrency,
                    lambda _index, call: count_input_tokens(
                        requests[call.fingerprint], api_key=api_key, client=client
                    ),
                )
        except OpenAIEvalError as exc:
            print(str(exc), file=sys.stderr)
            return 1
        input_counts = {
            call.fingerprint: count
            for call, count in zip(pending_calls, counts, strict=True)
        }
        input_count_method = INPUT_COUNT_OPENAI_EXACT
    elif args.reuse_exact_input_total is not None:
        input_counts = {call.fingerprint: 0 for call in pending_calls}
        if pending_calls:
            input_counts[pending_calls[0].fingerprint] = args.reuse_exact_input_total
        input_count_method = INPUT_COUNT_OPENAI_REUSED_TOTAL
    else:
        input_counts = {
            fingerprint: conservative_input_bound(request)
            for fingerprint, request in requests.items()
        }
        input_count_method = INPUT_COUNT_LOCAL_BOUND
    cache_rates = (
        args.cached_input_price_per_million,
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
        if args.kind == "v2-recall" and priced_cache_rates
        else rate
    )
    estimate = estimate_cost(
        pending_calls,
        input_counts=input_counts,
        prior_records=[],
        fallback_output_tokens=max_output_tokens,
        rate=budget_rate,
    )
    print_preflight(
        estimate,
        selected=len(prepared),
        resumed=len(resumed_calls),
        rate=budget_rate,
        input_label=(
            "counted input"
            if args.exact_input_counts or args.reuse_exact_input_total is not None
            else "bounded input"
        ),
    )
    if args.exact_input_counts:
        print("  input method       OpenAI Responses input-token endpoint")
    elif args.reuse_exact_input_total is not None:
        print("  input method       reused exact input-token dry-run total")
    else:
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
    run_failures: list[BaseException] = []
    evaluation_run_id = str(uuid.uuid4()) if args.kind == "v2-recall" else ""
    try:
        with JsonlRecorder(output_path) as recorder:
            if args.kind == "v2-recall" and pending_calls:
                if args.max_cost_usd is None:
                    raise AssertionError("paid OpenAI V2 run has no approved max cost")
                if args.input_price_per_million is None:
                    raise AssertionError(
                        "paid OpenAI V2 run has no explicit input rate"
                    )
                if args.output_price_per_million is None:
                    raise AssertionError(
                        "paid OpenAI V2 run has no explicit output rate"
                    )
                if args.cached_input_price_per_million is None:
                    raise AssertionError(
                        "paid OpenAI V2 run has no cached-input rate"
                    )
                if args.cache_write_price_per_million is None:
                    raise AssertionError(
                        "paid OpenAI V2 run has no cache-write rate"
                    )
                recorder.append(
                    make_run_manifest(
                        kind=v2_recall_eval.KIND,
                        evaluation_run_id=evaluation_run_id,
                        provider="openai",
                        model=args.model,
                        stage2_pack_fingerprint=stage2_pack_digest,
                        calls=pending_calls,
                        qualification_fingerprints={
                            call.fingerprint: (
                                v2_recall_eval.deployment_fingerprint(
                                    call.completion
                                )
                            )
                            for call in pending_calls
                        },
                        approved_max_cost_usd=args.max_cost_usd,
                        rates_per_million_usd={
                            "input": args.input_price_per_million,
                            "output": args.output_price_per_million,
                            "cached_input": args.cached_input_price_per_million,
                            "cache_write": args.cache_write_price_per_million,
                        },
                        input_count_method=input_count_method,
                        input_counts=input_counts,
                        estimate=estimate,
                        qualification_expires_at=args.qualification_expires_at,
                        safety_identifier_format_version=(
                            V2_EVAL_SAFETY_IDENTIFIER_FORMAT_VERSION
                        ),
                        safety_identifier=V2_EVAL_SAFETY_IDENTIFIER,
                    )
                )
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
                            recorder.append(
                                {**reusable[call.fingerprint], "resumed": True}
                            )
                    run_one = lambda _index, call: run_case(  # noqa: E731
                        call,
                        recorder,
                        api_key=api_key,
                        client=client,
                        stage2_pack_fingerprint=stage2_pack_digest,
                        evaluation_run_id=evaluation_run_id,
                    )
                    if args.kind == "v2-recall":
                        outcomes = await run_bounded_collect(
                            level_pending, args.concurrency, run_one
                        )
                    else:
                        outcomes = await run_bounded(
                            level_pending, args.concurrency, run_one
                        )
                    new_by_fingerprint: dict[str, Any] = {}
                    level_failed = False
                    for call, outcome in zip(
                        level_pending, outcomes, strict=True
                    ):
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
                    if args.kind == "scoring":
                        print_scoring_results(label, results, verbose=args.verbose)
                    elif args.kind == "v2-recall":
                        print_v2_recall_results(label, results, verbose=args.verbose)
                    elif args.kind == "reattempt":
                        print_reattempt_results(label, results, verbose=args.verbose)
                    else:
                        print_evidence_results(label, results, verbose=args.verbose)
    except OpenAIEvalError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    if args.kind == "scoring":
        effort_sweep.print_summary(by_level)
    elif args.kind == "evidence":
        structured_evidence_eval.print_summary(by_level)
    if args.kind == "v2-recall" and new_records:
        if args.cached_input_price_per_million is None:
            raise AssertionError("paid OpenAI V2 run has no cached-input rate")
        if args.cache_write_price_per_million is None:
            raise AssertionError("paid OpenAI V2 run has no cache-write rate")
        try:
            paid_cost = openai_actual_cost(
                new_records,
                input_per_million=rate.input_per_million,
                output_per_million=rate.output_per_million,
                cached_input_per_million=args.cached_input_price_per_million,
                cache_write_per_million=args.cache_write_price_per_million,
            )
        except OpenAIEvalError as exc:
            print(str(exc), file=sys.stderr)
            return 1
    else:
        paid_cost = generic_actual_cost(new_records, rate)
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
        return 1
    if args.enforce_reviewed_gate:
        failures = effort_sweep.reviewed_gate_failures(by_level)
        if failures:
            print("\nreviewed gate failed:", file=sys.stderr)
            for failure in failures:
                print(f"  - {failure}", file=sys.stderr)
            return 1
        print("reviewed gate passed")
    if args.enforce_secondary_bucket_gate:
        failures = effort_sweep.secondary_bucket_gate_failures(by_level)
        if failures:
            print("\nsecondary bucket gate failed:", file=sys.stderr)
            for failure in failures:
                print(f"  - {failure}", file=sys.stderr)
            return 1
        print("secondary bucket gate passed")
    if args.enforce_evidence_gate:
        failures = structured_evidence_eval.gate_failures(by_level)
        if failures:
            print("\nstructured-evidence gate failed:", file=sys.stderr)
            for failure in failures:
                print(f"  - {failure}", file=sys.stderr)
            return 1
        print("structured-evidence gate passed")
    if args.enforce_v2_recall_gate:
        failures = v2_recall_eval.qualification_gate_failures(by_level)
        if failures:
            print("\nV2 Recall gate failed:", file=sys.stderr)
            for failure in failures:
                print(f"  - {failure}", file=sys.stderr)
            return 1
        print("V2 Recall gate passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
