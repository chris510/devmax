#!/usr/bin/env python
"""Prepare, submit, and collect cost-guarded OpenAI Batch scoring evaluations.

The submit command has a free dry-run and refuses to upload any case carrying
an explicit review status other than ``approved``. Batch collection writes the
same fingerprinted JSONL result format as the standard-latency runner, so later
stages can resume without paying for an identical request twice.

Examples:
    uv run python scripts/openai_batch_bakeoff.py submit \
      scripts/grounded_effort_cases_week1.json \
      scripts/grounded_effort_cases_week1_release.json \
      --grounding-manifest cards.json --exclude-tag risk-smoke --dry-run
    uv run python scripts/openai_batch_bakeoff.py collect \
      .eval-results/openai-batch-state-....json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts import effort_sweep, openai_bakeoff  # noqa: E402
from scripts.effort_sweep_support import (  # noqa: E402
    JsonlRecorder,
    PreparedCall,
    actual_cost,
    batch_rate_for_model,
    enforce_budget,
    estimate_cost,
    hydrate_grounding,
    load_cases,
    load_result_records,
    make_result_record,
    output_path_for,
    pending_case_reviews,
    positive_decimal,
    print_preflight,
    select_cases,
)
from scripts.openai_eval_support import (  # noqa: E402
    REQUEST_TIMEOUT_SECONDS,
    OpenAIEvalError,
    conservative_input_bound,
    parse_response,
    response_request,
)

FILES_URL = "https://api.openai.com/v1/files"
BATCHES_URL = "https://api.openai.com/v1/batches"
BATCH_ENDPOINT = "/v1/responses"
BATCH_COMPLETION_WINDOW = "24h"
BATCH_FILE_LIMIT_BYTES = 200 * 1024 * 1024
STATE_FORMAT_VERSION = 1
DEFAULT_MODEL = "gpt-5.6-terra"
DEFAULT_EFFORT = "medium"
DEFAULT_OUTPUT_CAP = 1024


def _api_error(response: httpx.Response) -> OpenAIEvalError:
    try:
        body = response.json()
        message = body.get("error", {}).get("message")
    except (ValueError, AttributeError):
        message = None
    return OpenAIEvalError(
        f"OpenAI API returned HTTP {response.status_code}: "
        f"{message or response.reason_phrase}"
    )


def _json_response(response: httpx.Response) -> dict[str, Any]:
    if not response.is_success:
        raise _api_error(response)
    try:
        payload = response.json()
    except ValueError as exc:
        raise OpenAIEvalError("OpenAI API returned invalid JSON") from exc
    if not isinstance(payload, dict):
        raise OpenAIEvalError("OpenAI API returned a non-object response")
    return payload


def batch_input_line(prepared: PreparedCall, custom_id: str) -> dict[str, Any]:
    """Build one official Batch request line without changing request semantics."""
    return {
        "custom_id": custom_id,
        "method": "POST",
        "url": BATCH_ENDPOINT,
        "body": response_request(prepared.completion, kind=prepared.kind),
    }


def build_batch_input(
    prepared: list[PreparedCall],
) -> tuple[str, list[dict[str, Any]]]:
    calls: list[dict[str, Any]] = []
    lines: list[str] = []
    for sequence, call in enumerate(prepared, 1):
        custom_id = f"devmax-{sequence:04d}-{call.fingerprint[:16]}"
        lines.append(
            json.dumps(
                batch_input_line(call, custom_id),
                ensure_ascii=False,
                separators=(",", ":"),
            )
        )
        calls.append(
            {
                "custom_id": custom_id,
                "index": call.index,
                "case": call.case,
                "kind": call.kind,
                "effort": call.effort,
                "completion": call.completion,
                "fingerprint": call.fingerprint,
            }
        )
    return "\n".join(lines) + ("\n" if lines else ""), calls


async def submit_requests(
    input_jsonl: str,
    *,
    api_key: str,
    metadata: dict[str, str],
    client: httpx.AsyncClient,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Upload one JSONL file and create its 24-hour Responses batch."""
    headers = {"Authorization": f"Bearer {api_key}"}
    try:
        uploaded = await client.post(
            FILES_URL,
            headers=headers,
            data={"purpose": "batch"},
            files={
                "file": (
                    "devmax-openai-scoring.jsonl",
                    input_jsonl.encode("utf-8"),
                    "application/jsonl",
                )
            },
        )
    except httpx.HTTPError as exc:
        raise OpenAIEvalError(f"OpenAI Batch input upload failed: {exc}") from exc
    upload = _json_response(uploaded)
    input_file_id = upload.get("id")
    if not isinstance(input_file_id, str) or not input_file_id:
        raise OpenAIEvalError("OpenAI file upload returned no file id")

    try:
        created = await client.post(
            BATCHES_URL,
            headers={**headers, "Content-Type": "application/json"},
            json={
                "input_file_id": input_file_id,
                "endpoint": BATCH_ENDPOINT,
                "completion_window": BATCH_COMPLETION_WINDOW,
                "metadata": metadata,
            },
        )
    except httpx.HTTPError as exc:
        raise OpenAIEvalError(
            f"OpenAI Batch creation failed after uploading {input_file_id}: {exc}"
        ) from exc
    return upload, _json_response(created)


def _prepared_from_state(raw: dict[str, Any]) -> PreparedCall:
    try:
        case = raw["case"]
        return PreparedCall(
            index=int(raw["index"]),
            case=case,
            case_name=str(case.get("name") or case.get("topic")),
            kind=str(raw["kind"]),
            effort=raw.get("effort"),
            completion=raw["completion"],
            fingerprint=str(raw["fingerprint"]),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("Batch state contains an invalid prepared call") from exc


def parse_batch_output(
    content: str, expected_custom_ids: set[str]
) -> dict[str, dict[str, Any]]:
    """Validate unordered Batch output before converting it to eval records."""
    output: dict[str, dict[str, Any]] = {}
    for line_number, line in enumerate(content.splitlines(), 1):
        if not line.strip():
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Batch output line {line_number} is invalid JSON") from exc
        if not isinstance(item, dict):
            raise ValueError(f"Batch output line {line_number} is not an object")
        custom_id = item.get("custom_id")
        if custom_id not in expected_custom_ids:
            raise ValueError(f"Batch output has unknown custom_id: {custom_id}")
        if custom_id in output:
            raise ValueError(f"Batch output repeats custom_id: {custom_id}")
        output[custom_id] = item
    missing = sorted(expected_custom_ids - output.keys())
    if missing:
        raise ValueError(f"Batch output is missing {len(missing)} request(s)")
    return output


def _state_path(requested: Path | None) -> Path:
    if requested is not None:
        return requested
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S.%fZ")
    return Path(".eval-results") / f"openai-batch-state-{stamp}.json"


def _write_state(path: Path, state: dict[str, Any], *, exclusive: bool) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    mode = "x" if exclusive else "w"
    with path.open(mode, encoding="utf-8") as destination:
        json.dump(state, destination, ensure_ascii=False, indent=2, sort_keys=True)
        destination.write("\n")


def _load_state(path: Path) -> dict[str, Any]:
    try:
        state = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read Batch state {path}: {exc}") from exc
    if not isinstance(state, dict) or state.get("format_version") != STATE_FORMAT_VERSION:
        raise ValueError(f"unsupported Batch state: {path}")
    return state


def _load_combined_cases(paths: list[Path], parser) -> list[dict[str, Any]]:
    combined: list[dict[str, Any]] = []
    for path in paths:
        combined.extend(load_cases(path, parser))
    return combined


def _submit_parser(subparsers) -> None:
    parser = subparsers.add_parser("submit", help="preflight or submit a scoring Batch")
    parser.add_argument("cases", type=Path, nargs="+")
    parser.add_argument("--grounding-manifest", type=Path, required=True)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--levels", nargs="+", default=[DEFAULT_EFFORT])
    parser.add_argument(
        "--max-output-tokens",
        type=openai_bakeoff.positive_int,
        default=DEFAULT_OUTPUT_CAP,
    )
    parser.add_argument("--case", action="append", dest="case_names")
    parser.add_argument("--tag", action="append", dest="tags")
    parser.add_argument("--exclude-tag", action="append", dest="exclude_tags")
    parser.add_argument("--resume", type=Path)
    parser.add_argument("--fresh", action="store_true")
    parser.add_argument("--state", type=Path)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--max-cost-usd", type=positive_decimal)
    parser.add_argument("--input-price-per-million", type=positive_decimal)
    parser.add_argument("--output-price-per-million", type=positive_decimal)


def _collect_parser(subparsers) -> None:
    parser = subparsers.add_parser("collect", help="collect one completed Batch")
    parser.add_argument("state", type=Path)
    parser.add_argument("--output", type=Path)


async def _submit(args, parser) -> int:
    cases = select_cases(
        _load_combined_cases(args.cases, parser),
        names=args.case_names,
        tags=args.tags,
        parser=parser,
    )
    excluded = set(args.exclude_tags or [])
    if excluded:
        cases = [case for case in cases if not (set(case.get("tags", [])) & excluded)]
        if not cases:
            parser.error("--exclude-tag removed every selected case")
    cases = hydrate_grounding(cases, args.grounding_manifest, parser)
    pending_reviews = pending_case_reviews(cases)

    prepared = openai_bakeoff.prepare_cases(
        cases,
        kind="scoring",
        levels=list(dict.fromkeys(args.levels)),
        model=args.model,
        max_output_tokens=args.max_output_tokens,
    )
    try:
        prior = load_result_records(args.resume, kind="scoring")
        rate = batch_rate_for_model(
            args.model,
            input_override=args.input_price_per_million,
            output_override=args.output_price_per_million,
        )
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    reusable = {} if args.fresh else prior
    pending = [call for call in prepared if call.fingerprint not in reusable]
    requests = {
        call.fingerprint: response_request(call.completion, kind=call.kind)
        for call in pending
    }
    input_counts = {
        fingerprint: conservative_input_bound(request)
        for fingerprint, request in requests.items()
    }
    estimate = estimate_cost(
        pending,
        input_counts=input_counts,
        prior_records=[],
        fallback_output_tokens=args.max_output_tokens,
        rate=rate,
    )
    print_preflight(
        estimate,
        selected=len(prepared),
        resumed=len(prepared) - len(pending),
        rate=rate,
        input_label="bounded input",
    )
    input_jsonl, state_calls = build_batch_input(pending)
    input_bytes = len(input_jsonl.encode("utf-8"))
    print(f"  Batch input file   {input_bytes} bytes")
    print(f"  human review       {len(pending_reviews)} case label(s) pending")
    if input_bytes > BATCH_FILE_LIMIT_BYTES:
        parser.error("Batch input exceeds the 200 MB API limit")
    enforce_budget(
        estimate,
        budget=args.max_cost_usd,
        dry_run=args.dry_run,
        parser=parser,
    )
    if args.dry_run:
        print("  dry run complete — no file upload or paid Batch request was made")
        return 0
    if pending_reviews:
        parser.error(
            "Batch not submitted: every explicit review_status must be approved"
        )
    if not pending:
        print("  nothing to submit — every fingerprint was resumed")
        return 0

    api_key = (
        os.environ.get("OPENAI_API_KEY", "").strip()
        or openai_bakeoff.EvalSettings().openai_api_key.strip()
    )
    if not api_key:
        print(
            "OPENAI_API_KEY is unset — ChatGPT credits cannot authorize API calls.",
            file=sys.stderr,
        )
        return 1
    state_path = _state_path(args.state)
    if state_path.exists():
        parser.error(f"state already exists: {state_path}")

    try:
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT_SECONDS) as client:
            upload, batch = await submit_requests(
                input_jsonl,
                api_key=api_key,
                metadata={"devmax_eval": "scoring", "model": args.model},
                client=client,
            )
    except OpenAIEvalError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    batch_id = batch.get("id")
    if not isinstance(batch_id, str) or not batch_id:
        print("OpenAI Batch creation returned no batch id", file=sys.stderr)
        return 1
    state = {
        "format_version": STATE_FORMAT_VERSION,
        "created_at": datetime.now(UTC).isoformat(),
        "status": str(batch.get("status", "validating")),
        "input_file_id": upload["id"],
        "batch_id": batch_id,
        "model": args.model,
        "rate": {
            **asdict(rate),
            "input_per_million": str(rate.input_per_million),
            "output_per_million": str(rate.output_per_million),
        },
        "estimated_cost_usd": str(estimate.usd),
        "calls": state_calls,
    }
    _write_state(state_path, state, exclusive=True)
    print(f"  submitted Batch    {batch_id}")
    print(f"  state              {state_path}")
    return 0


async def _collect(args, parser) -> int:
    try:
        state = _load_state(args.state)
        calls = state["calls"]
        batch_id = state["batch_id"]
        if not isinstance(calls, list) or not isinstance(batch_id, str):
            raise ValueError("Batch state is missing calls or batch_id")
    except (KeyError, ValueError) as exc:
        parser.error(str(exc))

    api_key = (
        os.environ.get("OPENAI_API_KEY", "").strip()
        or openai_bakeoff.EvalSettings().openai_api_key.strip()
    )
    if not api_key:
        print(
            "OPENAI_API_KEY is unset — ChatGPT credits cannot authorize API calls.",
            file=sys.stderr,
        )
        return 1
    headers = {"Authorization": f"Bearer {api_key}"}
    try:
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT_SECONDS) as client:
            status_response = await client.get(f"{BATCHES_URL}/{batch_id}", headers=headers)
            batch = _json_response(status_response)
            state["status"] = str(batch.get("status", "unknown"))
            state["request_counts"] = batch.get("request_counts")
            _write_state(args.state, state, exclusive=False)
            if batch.get("status") != "completed":
                print(
                    f"Batch {batch_id} is {batch.get('status', 'unknown')}; "
                    f"counts={batch.get('request_counts')}"
                )
                return 2
            output_file_id = batch.get("output_file_id")
            if not isinstance(output_file_id, str) or not output_file_id:
                raise OpenAIEvalError("completed Batch has no output_file_id")
            output_response = await client.get(
                f"{FILES_URL}/{output_file_id}/content", headers=headers
            )
            if not output_response.is_success:
                raise _api_error(output_response)
            output_by_id = parse_batch_output(
                output_response.text,
                {str(call["custom_id"]) for call in calls},
            )
    except (OpenAIEvalError, ValueError, KeyError) as exc:
        print(str(exc), file=sys.stderr)
        return 1

    output_path = output_path_for(
        requested=args.output,
        resume=None,
        kind="openai-batch-scoring",
        parser=parser,
    )
    results: list[effort_sweep.Result] = []
    records: list[dict[str, Any]] = []
    try:
        with JsonlRecorder(output_path) as recorder:
            for raw_call in calls:
                prepared = _prepared_from_state(raw_call)
                item = output_by_id[str(raw_call["custom_id"])]
                response = item.get("response")
                if item.get("error") or not isinstance(response, dict):
                    raise OpenAIEvalError(
                        f"Batch request {raw_call['custom_id']} failed: {item.get('error')}"
                    )
                if response.get("status_code") != 200:
                    raise OpenAIEvalError(
                        f"Batch request {raw_call['custom_id']} returned "
                        f"HTTP {response.get('status_code')}"
                    )
                parsed = parse_response(response.get("body") or {})
                result = openai_bakeoff._scoring_result(
                    prepared, parsed.data, parsed.usage
                )
                payload = effort_sweep.result_payload(result)
                record = make_result_record(
                    prepared,
                    model=prepared.completion["model"],
                    result=payload,
                    usage=parsed.usage,
                )
                record.update(
                    {
                        "provider_response_id": parsed.response_id,
                        "provider_response_model": parsed.model,
                        "provider_elapsed_ms": None,
                        "provider_mode": "batch",
                        "batch_id": batch_id,
                        "batch_request_id": item.get("id"),
                    }
                )
                recorder.append(record)
                results.append(result)
                records.append(record)
    except (OpenAIEvalError, ValueError) as exc:
        print(
            f"{exc}. Successful earlier records remain durable at {output_path}.",
            file=sys.stderr,
        )
        return 1

    rate_raw = state.get("rate") or {}
    try:
        rate = batch_rate_for_model(
            str(state["model"]),
            input_override=positive_decimal(str(rate_raw["input_per_million"])),
            output_override=positive_decimal(str(rate_raw["output_per_million"])),
        )
    except (KeyError, ValueError, argparse.ArgumentTypeError) as exc:
        parser.error(f"invalid Batch rate in state: {exc}")
    label = str(calls[0].get("effort") or "default") if calls else "default"
    openai_bakeoff.print_scoring_results(label, results, verbose=False)
    effort_sweep.print_summary({label: results})
    print(f"\nBatch paid-call cost: ${actual_cost(records, rate):.4f}")
    print(f"results: {output_path}")
    state["status"] = "collected"
    state["results"] = str(output_path)
    _write_state(args.state, state, exclusive=False)
    return 0


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    _submit_parser(subparsers)
    _collect_parser(subparsers)
    args = parser.parse_args()
    if args.command == "submit":
        return await _submit(args, parser)
    return await _collect(args, parser)


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
