#!/usr/bin/env python
"""Compare one Claude V2 Recall run with three fresh Luna replicas.

This is an evidence reader, not a model runner.  It never opens a provider
client and it refuses to compare partially aligned or cherry-picked files.
Costs use explicit caller-supplied rates so a historical result cannot silently
inherit today's pricing.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
import uuid
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import OPENAI_V2_LUNA_MODEL, parse_strict_utc_timestamp  # noqa: E402
from app.services import llm  # noqa: E402
from scripts import openai_bakeoff, v2_recall_eval, v2_recall_sweep  # noqa: E402
from scripts.effort_sweep_support import (  # noqa: E402
    INPUT_COUNT_ANTHROPIC_EXACT,
    MAX_QUALIFICATION_WINDOW,
    RESULT_FORMAT_VERSION,
    RUN_MANIFEST_RECORD_TYPE,
    VALID_INPUT_COUNT_METHODS,
    PreparedCall,
    Usage,
    cost_ceiling_for_display,
    hydrate_grounding,
    load_cases,
    positive_decimal,
    qualification_expiry_arg,
    usage_from_record,
)
from scripts.effort_sweep_support import (  # noqa: E402
    MIN_OPENAI_V2_COST_REDUCTION as MIN_COST_REDUCTION,
)
from scripts.openai_eval_support import (  # noqa: E402
    V2_EVAL_SAFETY_IDENTIFIER,
    V2_EVAL_SAFETY_IDENTIFIER_FORMAT_VERSION,
)

_SEMANTIC_DIGEST_LENGTH = 64
_INVALID_FAILURE_MARKERS = ("contract", "invalid", "malformed", "parse", "schema")
_SUCCESS_RESULT_KEYS = {
    "expected_recall",
    "recall",
    "expected_flow",
    "flow",
    "expected_decision",
    "decision",
    "semantic_fingerprint",
    "feedback",
    "follow_up_question",
    "needs_more_evidence",
    "mastery_summary",
}
TEXT_QUALITY_REVIEW_FORMAT_VERSION = 3
TEXT_QUALITY_REVIEW_KIND = "v2_recall_text_quality_review"
TEXT_QUALITY_UNCONDITIONAL_CHECKS = (
    "source_grounded",
    "no_unsupported_correction",
    "no_numeric_secondary_axis_claim",
    "mastery_summary_recall_only",
    "mastery_summary_distinguishes_unaided_from_probe_assisted_recall",
    "no_broad_or_unmeasured_mastery_claim",
    "feedback_and_mastery_are_concise_and_direct",
)
TEXT_QUALITY_SCORE_CHECKS = (
    "low_recall_feedback_states_correct_essential_account",
    "passing_feedback_is_appropriately_direct",
)
TEXT_QUALITY_REVIEW_CHECKS = (
    *TEXT_QUALITY_UNCONDITIONAL_CHECKS,
    *TEXT_QUALITY_SCORE_CHECKS,
)


@dataclass(frozen=True)
class Outcome:
    case: str
    semantic_fingerprint: str
    expected_recall: int
    expected_flow: str
    usage: Usage
    record: dict[str, Any]
    result: v2_recall_eval.Result | None = None
    failure_type: str = ""
    failure_message: str = ""
    elapsed_ms: float | None = None

    @property
    def succeeded(self) -> bool:
        return self.result is not None

    @property
    def invalid(self) -> bool:
        normalized = self.failure_type.lower()
        return any(marker in normalized for marker in _INVALID_FAILURE_MARKERS)


@dataclass(frozen=True)
class RunInvocation:
    fingerprint: str
    case: str
    effort: str | None
    qualification_fingerprint: str


@dataclass(frozen=True)
class PreflightAttestation:
    approved_max_cost_usd: Decimal
    estimated_ceiling_usd: Decimal
    rates_per_million_usd: dict[str, Decimal]
    input_count_method: str
    input_counts: dict[str, int]
    input_tokens_total: int
    estimated_output_tokens: int
    estimated_output_tokens_per_call: int


@dataclass(frozen=True)
class SafetyIdentifierAttestation:
    kind: str
    format_version: int
    value: str


@dataclass(frozen=True)
class RunManifest:
    evaluation_run_id: str
    provider: str
    model: str
    created_at: datetime
    qualification_expires_at: datetime
    stage2_pack_fingerprint: str
    invocations: dict[str, RunInvocation]
    preflight: PreflightAttestation
    safety_identifier: SafetyIdentifierAttestation | None
    digest: str


@dataclass(frozen=True)
class EvidenceRun:
    label: str
    path: Path
    provider: str
    model: str
    effort: str | None
    created_at: datetime
    qualification_expires_at: datetime
    qualification_fingerprint: str
    stage2_pack_fingerprint: str
    evaluation_run_id: str
    outcomes: dict[str, Outcome]
    file_digest: str
    record_digests: frozenset[str]
    response_ids: frozenset[str]
    manifest_digest: str
    invocations: dict[str, RunInvocation]
    preflight: PreflightAttestation
    safety_identifier: SafetyIdentifierAttestation | None

    @property
    def results(self) -> list[v2_recall_eval.Result]:
        return [
            outcome.result
            for outcome in self.outcomes.values()
            if outcome.result is not None
        ]


@dataclass(frozen=True)
class TextQualityCaseReview:
    case: str
    semantic_fingerprint: str
    provider_response_id: str
    feedback_sha256: str
    mastery_summary_sha256: str
    prepared_request_fingerprint: str
    trusted_case_sha256: str
    trusted_case: dict[str, Any]
    recall: int
    flow: str
    feedback: str
    mastery_summary: str
    notes: str
    checks: dict[str, bool]


@dataclass(frozen=True)
class TextQualityAttestation:
    path: Path
    evaluation_run_id: str
    evidence_sha256: str
    manifest_sha256: str
    stage2_pack_fingerprint: str
    model: str
    reviewer: str
    reviewed_at: datetime
    notes: str
    case_reviews: dict[str, TextQualityCaseReview]


@dataclass(frozen=True)
class FrozenCaseBinding:
    case: dict[str, Any]
    prepared: PreparedCall
    semantic_fingerprint: str


@dataclass(frozen=True)
class RunMetrics:
    label: str
    attempts: int
    successes: int
    invalids: int
    failures: int
    exact: int
    within_one: int
    behavioral_matches: int
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_write_tokens: int
    latency_samples: tuple[float, ...]
    cost_usd: Decimal

    @property
    def cost_per_success(self) -> Decimal | None:
        if self.successes == 0:
            return None
        return self.cost_usd / Decimal(self.successes)


@dataclass(frozen=True)
class ClaudeRates:
    input_per_million: Decimal
    output_per_million: Decimal
    cache_read_per_million: Decimal
    cache_write_per_million: Decimal


@dataclass(frozen=True)
class LunaRates:
    input_per_million: Decimal
    output_per_million: Decimal
    cached_input_per_million: Decimal
    cache_write_per_million: Decimal


@dataclass(frozen=True)
class DecisionDisagreement:
    label: str
    case: str
    expected: dict[str, str]
    actual: dict[str, str]


@dataclass(frozen=True)
class ReplicaDisagreement:
    label: str
    case: str
    claude: dict[str, str]
    luna: dict[str, str]


@dataclass(frozen=True)
class ComparisonReport:
    metrics: tuple[RunMetrics, ...]
    luna_aggregate: RunMetrics
    decision_confusion: dict[str, Counter[tuple[str, str]]]
    human_disagreements: tuple[DecisionDisagreement, ...]
    replica_disagreements: tuple[ReplicaDisagreement, ...]
    text_quality_review_count: int
    cost_reduction: Decimal | None
    qualification_expires_at: datetime
    gate_failures: tuple[str, ...]

    @property
    def passed(self) -> bool:
        return not self.gate_failures


def _is_int(value: Any) -> bool:
    return type(value) is int


def _semantic_digest(value: Any, *, location: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != _SEMANTIC_DIGEST_LENGTH
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise ValueError(f"{location}: semantic_fingerprint must be a lowercase SHA-256")
    return value


def _sha256_digest(value: Any, *, location: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise ValueError(f"{location}: must be a lowercase SHA-256")
    return value


def _text_sha256(value: str) -> str:
    """Hash the exact decoded text value with no trimming or normalization."""
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _canonical_sha256(value: Any) -> str:
    encoded = json.dumps(
        value, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _nonempty_text(value: Any, *, location: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{location}: must be non-empty text")
    return value.strip()


def _strict_utc_timestamp(value: Any, *, location: str) -> datetime:
    if not isinstance(value, str):
        raise ValueError(f"{location}: must be an ISO-8601 UTC timestamp")
    try:
        return parse_strict_utc_timestamp(value, field=location)
    except ValueError as exc:
        raise ValueError(str(exc)) from exc


def _reviewed_at(value: Any, *, location: str) -> datetime:
    reviewed_at = _nonempty_text(value, location=location)
    try:
        parsed = datetime.fromisoformat(reviewed_at.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{location}: must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{location}: must include a timezone")
    return parsed.astimezone(UTC)


def _object_without_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError(f"duplicate JSON key {key!r}")
        value[key] = item
    return value


def _expected_fields(
    record: dict[str, Any], payload: dict[str, Any], *, location: str
) -> tuple[int, str, str]:
    expected_recall = payload.get("expected_recall", record.get("expected_recall"))
    expected_flow = payload.get("expected_flow", record.get("expected_flow"))
    semantic = payload.get(
        "semantic_fingerprint", record.get("semantic_fingerprint")
    )
    if not _is_int(expected_recall) or expected_recall not in range(6):
        raise ValueError(f"{location}: expected_recall must be an integer from 0 to 5")
    if expected_flow not in v2_recall_eval.VALID_FLOWS:
        raise ValueError(f"{location}: expected_flow is invalid")
    return (
        expected_recall,
        expected_flow,
        _semantic_digest(semantic, location=location),
    )


def _strict_usage(record: dict[str, Any], *, location: str) -> Usage:
    try:
        usage = usage_from_record(record)
    except ValueError as exc:
        raise ValueError(f"{location}: invalid usage") from exc
    for field in (
        "input_tokens",
        "output_tokens",
        "cache_read_tokens",
        "cache_write_tokens",
    ):
        value = getattr(usage, field)
        if not _is_int(value) or value < 0:
            raise ValueError(f"{location}: usage.{field} must be a non-negative integer")
    return usage


def _elapsed_ms(record: dict[str, Any], *, location: str) -> float | None:
    value = record.get("provider_elapsed_ms")
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
        raise ValueError(f"{location}: provider_elapsed_ms must be non-negative")
    return float(value)


def _success_outcome(
    record: dict[str, Any], *, line_number: int, location: str, usage: Usage
) -> Outcome:
    if usage.input_tokens == 0 or usage.output_tokens == 0:
        raise ValueError(
            f"{location}: successful evidence requires positive input and output tokens"
        )
    payload = record.get("result")
    if not isinstance(payload, dict):
        raise ValueError(f"{location}: result must be an object")
    if set(payload) != _SUCCESS_RESULT_KEYS:
        raise ValueError(
            f"{location}: result fields must be exactly {sorted(_SUCCESS_RESULT_KEYS)}"
        )
    if "failure" in record or "error" in record:
        raise ValueError(f"{location}: record cannot contain both result and failure")

    expected_recall, expected_flow, semantic = _expected_fields(
        record, payload, location=location
    )
    recall = payload.get("recall")
    flow = payload.get("flow")
    needs_more_evidence = payload.get("needs_more_evidence")
    feedback = payload.get("feedback")
    follow_up_question = payload.get("follow_up_question")
    mastery_summary = payload.get("mastery_summary")
    if not _is_int(recall) or recall not in range(6):
        raise ValueError(f"{location}: recall must be an integer from 0 to 5")
    if flow not in v2_recall_eval.VALID_FLOWS:
        raise ValueError(f"{location}: flow is invalid")
    if type(needs_more_evidence) is not bool:
        raise ValueError(f"{location}: needs_more_evidence must be boolean")
    if not isinstance(follow_up_question, str):
        raise ValueError(f"{location}: follow_up_question must be text")
    if not isinstance(feedback, str) or not feedback.strip():
        raise ValueError(f"{location}: feedback must be non-empty text")
    if not isinstance(mastery_summary, str) or not mastery_summary.strip():
        raise ValueError(f"{location}: mastery_summary must be non-empty text")

    case = record["case"]
    result = v2_recall_eval.Result(
        index=line_number - 1,
        case=case,
        expected_recall=expected_recall,
        recall=recall,
        expected_flow=expected_flow,
        flow=flow,
        semantic_fingerprint=semantic,
        usage=usage,
        feedback=feedback,
        follow_up_question=follow_up_question,
        needs_more_evidence=needs_more_evidence,
        mastery_summary=mastery_summary,
    )
    if payload.get("expected_decision") != result.expected_decision:
        raise ValueError(f"{location}: stored expected_decision does not match payload")
    if payload.get("decision") != result.decision:
        raise ValueError(f"{location}: stored decision does not match payload")
    top_semantic = record.get("semantic_fingerprint")
    if top_semantic is not None and top_semantic != semantic:
        raise ValueError(f"{location}: top-level semantic fingerprint does not match result")
    return Outcome(
        case=case,
        semantic_fingerprint=semantic,
        expected_recall=expected_recall,
        expected_flow=expected_flow,
        usage=usage,
        record=record,
        result=result,
        elapsed_ms=_elapsed_ms(record, location=location),
    )


def _failure_outcome(
    record: dict[str, Any], *, location: str, usage: Usage
) -> Outcome:
    failure = record.get("failure", record.get("error"))
    if failure is None:
        raise ValueError(f"{location}: record must contain result or an encoded failure")
    if isinstance(failure, dict):
        payload = failure
        failure_type = payload.get("type", payload.get("kind", "failure"))
        failure_message = payload.get("message", "")
    elif isinstance(failure, str):
        payload = {}
        failure_type = "failure"
        failure_message = failure
    else:
        raise ValueError(f"{location}: encoded failure must be an object or string")
    if not isinstance(failure_type, str) or not failure_type.strip():
        raise ValueError(f"{location}: failure type must be a non-empty string")
    if not isinstance(failure_message, str):
        raise ValueError(f"{location}: failure message must be a string")
    expected_recall, expected_flow, semantic = _expected_fields(
        record, payload, location=location
    )
    top_semantic = record.get("semantic_fingerprint")
    if top_semantic is not None and top_semantic != semantic:
        raise ValueError(f"{location}: top-level semantic fingerprint does not match failure")
    return Outcome(
        case=record["case"],
        semantic_fingerprint=semantic,
        expected_recall=expected_recall,
        expected_flow=expected_flow,
        usage=usage,
        record=record,
        failure_type=failure_type.strip(),
        failure_message=failure_message.strip(),
        elapsed_ms=_elapsed_ms(record, location=location),
    )


def _record_digest(record: dict[str, Any]) -> str:
    encoded = json.dumps(
        record, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def _decimal_text(
    value: Any, *, location: str, allow_zero: bool = False
) -> Decimal:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{location}: must be a decimal string")
    try:
        parsed = Decimal(value)
    except InvalidOperation as exc:
        raise ValueError(f"{location}: must be a decimal string") from exc
    if not parsed.is_finite() or parsed < 0 or (not allow_zero and parsed == 0):
        qualifier = "non-negative" if allow_zero else "positive"
        raise ValueError(f"{location}: must be a {qualifier} finite decimal")
    return parsed


def _parse_preflight(
    raw: Any,
    *,
    location: str,
    invocations: dict[str, RunInvocation],
) -> PreflightAttestation:
    required_keys = {
        "approved_max_cost_usd",
        "estimated_ceiling_usd",
        "rates_per_million_usd",
        "input_count_method",
        "input_counts",
        "input_tokens_total",
        "estimated_output_tokens",
        "estimated_output_tokens_per_call",
    }
    if not isinstance(raw, dict) or set(raw) != required_keys:
        raise ValueError(
            f"{location}: fields must be exactly {sorted(required_keys)}"
        )
    approved = _decimal_text(
        raw["approved_max_cost_usd"],
        location=f"{location}: approved_max_cost_usd",
    )
    ceiling = _decimal_text(
        raw["estimated_ceiling_usd"],
        location=f"{location}: estimated_ceiling_usd",
    )
    if approved < ceiling:
        raise ValueError(f"{location}: approved max cost is below estimated ceiling")
    raw_rates = raw["rates_per_million_usd"]
    rate_keys = {"input", "output", "cached_input", "cache_write"}
    if not isinstance(raw_rates, dict) or set(raw_rates) != rate_keys:
        raise ValueError(f"{location}: all four explicit rates are required")
    rates = {
        key: _decimal_text(
            raw_rates[key], location=f"{location}: rates_per_million_usd.{key}"
        )
        for key in sorted(rate_keys)
    }
    method = raw["input_count_method"]
    if method not in VALID_INPUT_COUNT_METHODS:
        raise ValueError(f"{location}: unsupported input-count method")
    raw_counts = raw["input_counts"]
    if not isinstance(raw_counts, dict) or set(raw_counts) != set(invocations):
        raise ValueError(f"{location}: input counts must match every invocation")
    if any(type(value) is not int or value < 0 for value in raw_counts.values()):
        raise ValueError(
            f"{location}: input counts must be non-negative exact integers"
        )
    counts = {fingerprint: raw_counts[fingerprint] for fingerprint in invocations}
    input_total = raw["input_tokens_total"]
    output_total = raw["estimated_output_tokens"]
    output_per_call = raw["estimated_output_tokens_per_call"]
    if type(input_total) is not int or input_total < 0:
        raise ValueError(f"{location}: input token total must be non-negative")
    if input_total != sum(counts.values()):
        raise ValueError(f"{location}: input token total does not match counts")
    if type(output_total) is not int or output_total < 0:
        raise ValueError(f"{location}: estimated output total must be non-negative")
    if type(output_per_call) is not int or output_per_call < 0:
        raise ValueError(
            f"{location}: estimated output tokens per call must be non-negative"
        )
    if output_total != output_per_call * len(invocations):
        raise ValueError(f"{location}: estimated output total does not match calls")
    estimated_cost = (
        Decimal(input_total)
        * max(rates["input"], rates["cached_input"], rates["cache_write"])
        + Decimal(output_total) * rates["output"]
    ) / Decimal(1_000_000)
    if ceiling != cost_ceiling_for_display(estimated_cost):
        raise ValueError(
            f"{location}: estimated ceiling does not match counts and rates"
        )
    return PreflightAttestation(
        approved_max_cost_usd=approved,
        estimated_ceiling_usd=ceiling,
        rates_per_million_usd=rates,
        input_count_method=method,
        input_counts=counts,
        input_tokens_total=input_total,
        estimated_output_tokens=output_total,
        estimated_output_tokens_per_call=output_per_call,
    )


def _parse_safety_identifier(
    raw: Any, *, location: str, provider: str
) -> SafetyIdentifierAttestation | None:
    if provider == "claude":
        if raw is not None:
            raise ValueError(
                f"{location}: Claude manifest cannot contain a safety identifier"
            )
        return None

    required_keys = {"kind", "format_version", "value"}
    if not isinstance(raw, dict) or set(raw) != required_keys:
        raise ValueError(
            f"{location}: fields must be exactly {sorted(required_keys)}"
        )
    if raw["kind"] != "synthetic_non_user":
        raise ValueError(f"{location}: identifier must be marked synthetic non-user")
    if raw["format_version"] != V2_EVAL_SAFETY_IDENTIFIER_FORMAT_VERSION:
        raise ValueError(f"{location}: unsupported safety-identifier format version")
    if raw["value"] != V2_EVAL_SAFETY_IDENTIFIER:
        raise ValueError(f"{location}: unexpected evaluation safety identifier")
    return SafetyIdentifierAttestation(
        kind=raw["kind"],
        format_version=raw["format_version"],
        value=raw["value"],
    )


def _parse_run_manifest(
    record: dict[str, Any], *, location: str, provider: str
) -> RunManifest:
    required_manifest_keys = {
        "format_version",
        "record_type",
        "kind",
        "created_at",
        "qualification_expires_at",
        "evaluation_run_id",
        "provider",
        "model",
        "stage2_pack_fingerprint",
        "fresh",
        "safety_identifier",
        "preflight",
        "invocations",
    }
    if set(record) != required_manifest_keys:
        raise ValueError(
            f"{location}: run manifest fields must be exactly "
            f"{sorted(required_manifest_keys)}"
        )
    if record.get("format_version") != RESULT_FORMAT_VERSION:
        raise ValueError(f"{location}: unsupported run manifest format")
    if record.get("record_type") != RUN_MANIFEST_RECORD_TYPE:
        raise ValueError(f"{location}: first row must be a run manifest")
    if record.get("kind") != v2_recall_eval.KIND:
        raise ValueError(f"{location}: run manifest has the wrong kind")
    manifest_provider = "anthropic" if provider == "claude" else "openai"
    if record.get("provider") != manifest_provider:
        raise ValueError(
            f"{location}: run manifest provider must be {manifest_provider!r}"
        )
    if record.get("fresh") is not True:
        raise ValueError(f"{location}: run manifest must be marked fresh")
    created_at = _strict_utc_timestamp(
        record.get("created_at"), location=f"{location}: created_at"
    )
    qualification_expires_at = _strict_utc_timestamp(
        record.get("qualification_expires_at"),
        location=f"{location}: qualification_expires_at",
    )
    if qualification_expires_at <= created_at:
        raise ValueError(f"{location}: qualification expiry must follow run creation")
    if qualification_expires_at > created_at + MAX_QUALIFICATION_WINDOW:
        raise ValueError(
            f"{location}: qualification expiry exceeds the 30-day evidence window"
        )
    try:
        run_id = str(uuid.UUID(str(record.get("evaluation_run_id"))))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{location}: invalid manifest evaluation_run_id") from exc
    model = record.get("model")
    if not isinstance(model, str) or not model.strip():
        raise ValueError(f"{location}: run manifest model must be non-empty")
    pack = _semantic_digest(
        record.get("stage2_pack_fingerprint"),
        location=f"{location}: stage2_pack_fingerprint",
    )
    raw_invocations = record.get("invocations")
    if not isinstance(raw_invocations, list) or not raw_invocations:
        raise ValueError(f"{location}: run manifest must list paid invocations")
    invocations: dict[str, RunInvocation] = {}
    required_keys = {
        "fingerprint",
        "case",
        "effort",
        "qualification_fingerprint",
    }
    for index, raw in enumerate(raw_invocations, 1):
        item_location = f"{location}: invocation {index}"
        if not isinstance(raw, dict) or set(raw) != required_keys:
            raise ValueError(
                f"{item_location}: fields must be exactly {sorted(required_keys)}"
            )
        fingerprint = raw.get("fingerprint")
        case = raw.get("case")
        effort = raw.get("effort")
        if not isinstance(fingerprint, str) or not fingerprint:
            raise ValueError(f"{item_location}: fingerprint must be non-empty")
        if fingerprint in invocations:
            raise ValueError(f"{item_location}: duplicate invocation fingerprint")
        if not isinstance(case, str) or not case.strip():
            raise ValueError(f"{item_location}: case must be non-empty")
        if effort is not None and (
            not isinstance(effort, str) or not effort.strip()
        ):
            raise ValueError(f"{item_location}: effort must be null or non-empty")
        qualification = _semantic_digest(
            raw.get("qualification_fingerprint"),
            location=f"{item_location}: qualification_fingerprint",
        )
        invocations[fingerprint] = RunInvocation(
            fingerprint=fingerprint,
            case=case,
            effort=effort,
            qualification_fingerprint=qualification,
        )
    preflight = _parse_preflight(
        record.get("preflight"),
        location=f"{location}: preflight",
        invocations=invocations,
    )
    if provider == "claude" and (
        preflight.input_count_method != INPUT_COUNT_ANTHROPIC_EXACT
    ):
        raise ValueError(
            f"{location}: Claude qualification requires exact Anthropic input counts"
        )
    safety_identifier = _parse_safety_identifier(
        record.get("safety_identifier"),
        location=f"{location}: safety_identifier",
        provider=provider,
    )
    return RunManifest(
        evaluation_run_id=run_id,
        provider=manifest_provider,
        model=model,
        created_at=created_at,
        qualification_expires_at=qualification_expires_at,
        stage2_pack_fingerprint=pack,
        invocations=invocations,
        preflight=preflight,
        safety_identifier=safety_identifier,
        digest=_record_digest(record),
    )


def load_evidence(path: Path, *, label: str, provider: str) -> EvidenceRun:
    """Load and strictly validate one V2 JSONL result artifact."""
    raw = path.read_bytes()
    file_digest = hashlib.sha256(raw).hexdigest()
    outcomes: dict[str, Outcome] = {}
    cases: dict[str, str] = {}
    request_fingerprints: set[str] = set()
    record_digests: set[str] = set()
    response_ids: set[str] = set()
    models: set[str] = set()
    efforts: set[str | None] = set()
    qualification_fingerprints: set[str] = set()
    stage2_pack_fingerprints: set[str] = set()
    evaluation_run_ids: set[str] = set()
    manifest: RunManifest | None = None

    for line_number, line in enumerate(raw.decode("utf-8").splitlines(), 1):
        if not line.strip():
            continue
        location = f"{path}:{line_number}"
        try:
            record = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{location}: invalid JSON") from exc
        if not isinstance(record, dict):
            raise ValueError(f"{location}: record must be an object")
        if record.get("record_type") == RUN_MANIFEST_RECORD_TYPE:
            if line_number != 1 or manifest is not None:
                raise ValueError(f"{location}: run manifest must be the first row")
            manifest = _parse_run_manifest(
                record, location=location, provider=provider
            )
            continue
        if manifest is None:
            raise ValueError(f"{location}: evidence row precedes the run manifest")
        evidence_created_at = _strict_utc_timestamp(
            record.get("created_at"), location=f"{location}: created_at"
        )
        if evidence_created_at < manifest.created_at:
            raise ValueError(f"{location}: evidence predates its run manifest")
        if evidence_created_at >= manifest.qualification_expires_at:
            raise ValueError(
                f"{location}: evidence was recorded after qualification expiry"
            )
        if record.get("format_version") != RESULT_FORMAT_VERSION:
            raise ValueError(f"{location}: unsupported result format")
        if record.get("kind") != v2_recall_eval.KIND:
            raise ValueError(f"{location}: expected kind {v2_recall_eval.KIND!r}")
        if record.get("scoring_prompt_variant") != "production":
            raise ValueError(f"{location}: only production prompt results are comparable")
        case = record.get("case")
        if not isinstance(case, str) or not case.strip():
            raise ValueError(f"{location}: case must be a non-empty string")
        model = record.get("model")
        if not isinstance(model, str) or not model.strip():
            raise ValueError(f"{location}: model must be a non-empty string")
        models.add(model)
        effort = record.get("effort")
        if effort is not None and (not isinstance(effort, str) or not effort.strip()):
            raise ValueError(f"{location}: effort must be null or non-empty text")
        efforts.add(effort)
        qualification_fingerprints.add(
            _semantic_digest(
                record.get("qualification_fingerprint"),
                location=f"{location}: qualification_fingerprint",
            )
        )
        stage2_pack_fingerprints.add(
            _semantic_digest(
                record.get("stage2_pack_fingerprint"),
                location=f"{location}: stage2_pack_fingerprint",
            )
        )
        request_fingerprint = record.get("fingerprint")
        if not isinstance(request_fingerprint, str) or not request_fingerprint:
            raise ValueError(f"{location}: missing request fingerprint")
        if request_fingerprint in request_fingerprints:
            raise ValueError(f"{location}: duplicate request fingerprint")
        request_fingerprints.add(request_fingerprint)
        invocation = manifest.invocations.get(request_fingerprint)
        if invocation is None:
            raise ValueError(
                f"{location}: evidence row was not selected in the run manifest"
            )
        if record.get("resumed") is True or (
            isinstance(record.get("result"), dict)
            and record["result"].get("resumed") is True
        ):
            raise ValueError(f"{location}: fresh comparison files cannot contain resumed rows")
        if record.get("fresh") is not True:
            raise ValueError(f"{location}: qualification evidence must be marked fresh")
        evaluation_run_id = record.get("evaluation_run_id")
        try:
            normalized_run_id = str(uuid.UUID(str(evaluation_run_id)))
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{location}: invalid evaluation_run_id") from exc
        evaluation_run_ids.add(normalized_run_id)
        if normalized_run_id != manifest.evaluation_run_id:
            raise ValueError(f"{location}: evaluation_run_id differs from manifest")
        if model != manifest.model:
            raise ValueError(f"{location}: model differs from run manifest")
        if case != invocation.case or effort != invocation.effort:
            raise ValueError(f"{location}: invocation identity differs from manifest")
        if record.get("stage2_pack_fingerprint") != (
            manifest.stage2_pack_fingerprint
        ):
            raise ValueError(
                f"{location}: Stage 2 pack fingerprint differs from manifest"
            )
        if record.get("qualification_fingerprint") != (
            invocation.qualification_fingerprint
        ):
            raise ValueError(
                f"{location}: qualification fingerprint differs from manifest"
            )

        usage = _strict_usage(record, location=location)
        if provider == "luna" and (
            usage.cache_read_tokens + usage.cache_write_tokens
            > usage.input_tokens
        ):
            raise ValueError(
                f"{location}: Luna cached and cache-write tokens cannot exceed "
                "total input tokens"
            )
        evidence_outcome = record.get("evidence_outcome")
        if evidence_outcome == "success" and "result" in record:
            outcome = _success_outcome(
                record, line_number=line_number, location=location, usage=usage
            )
        elif evidence_outcome == "failure" and "result" not in record:
            outcome = _failure_outcome(record, location=location, usage=usage)
        else:
            raise ValueError(
                f"{location}: evidence_outcome must exactly match success/failure payload"
            )
        if outcome.semantic_fingerprint in outcomes:
            raise ValueError(f"{location}: duplicate semantic fingerprint")
        prior_semantic = cases.get(case)
        if prior_semantic is not None:
            raise ValueError(
                f"{location}: duplicate case {case!r} has semantic fingerprints "
                f"{prior_semantic[:12]} and {outcome.semantic_fingerprint[:12]}"
            )
        outcomes[outcome.semantic_fingerprint] = outcome
        cases[case] = outcome.semantic_fingerprint
        record_digests.add(_record_digest(record))
        response_id = record.get("provider_response_id")
        if provider == "luna" and outcome.succeeded and response_id is None:
            raise ValueError(
                f"{location}: successful Luna rows require provider_response_id "
                "to prove fresh replicas"
            )
        if response_id is not None:
            if not isinstance(response_id, str) or not response_id.strip():
                raise ValueError(f"{location}: provider_response_id must be non-empty")
            if response_id in response_ids:
                raise ValueError(f"{location}: duplicate provider_response_id")
            response_ids.add(response_id)
        response_model = record.get("provider_response_model")
        if outcome.succeeded:
            if not isinstance(response_model, str) or response_model != model:
                raise ValueError(
                    f"{location}: provider response model must exactly match {model!r}"
                )

    if manifest is None:
        raise ValueError(f"{path}: missing run manifest")
    missing_invocations = sorted(
        set(manifest.invocations) - request_fingerprints
    )
    if missing_invocations:
        raise ValueError(
            f"{path}: missing evidence for {len(missing_invocations)} manifest "
            "invocation(s)"
        )
    if not outcomes:
        raise ValueError(f"{path}: no V2 Recall records")
    if len(models) != 1:
        raise ValueError(f"{path}: every record must use one model, found {sorted(models)}")
    model = next(iter(models))
    if len(efforts) != 1:
        raise ValueError(f"{path}: every record must use one effort, found {efforts}")
    if len(qualification_fingerprints) != 1:
        raise ValueError(f"{path}: mixed qualification fingerprints")
    if len(stage2_pack_fingerprints) != 1:
        raise ValueError(f"{path}: mixed Stage 2 pack fingerprints")
    if len(evaluation_run_ids) != 1:
        raise ValueError(f"{path}: mixed evaluation run IDs")
    if provider == "claude" and not model.startswith("claude-"):
        raise ValueError(f"{path}: Claude evidence uses unexpected model {model!r}")
    if provider == "luna" and model != OPENAI_V2_LUNA_MODEL:
        raise ValueError(f"{path}: Luna evidence uses unexpected model {model!r}")
    return EvidenceRun(
        label=label,
        path=path,
        provider=provider,
        model=model,
        effort=next(iter(efforts)),
        created_at=manifest.created_at,
        qualification_expires_at=manifest.qualification_expires_at,
        qualification_fingerprint=next(iter(qualification_fingerprints)),
        stage2_pack_fingerprint=next(iter(stage2_pack_fingerprints)),
        evaluation_run_id=next(iter(evaluation_run_ids)),
        outcomes=outcomes,
        file_digest=file_digest,
        record_digests=frozenset(record_digests),
        response_ids=frozenset(response_ids),
        manifest_digest=manifest.digest,
        invocations=manifest.invocations,
        preflight=manifest.preflight,
        safety_identifier=manifest.safety_identifier,
    )


def _prepared_calls_for_run(
    run: EvidenceRun, reviewed_cases: Sequence[dict[str, Any]]
) -> list[PreparedCall]:
    cases = list(reviewed_cases)
    if run.provider == "claude":
        return v2_recall_sweep.prepare_cases(
            cases,
            levels=[run.effort],
            model=run.model,
        )
    if run.provider == "luna":
        output_cap = run.preflight.estimated_output_tokens_per_call
        if output_cap < 1:
            raise ValueError(f"{run.label}: OpenAI output cap must be positive")
        return openai_bakeoff.prepare_cases(
            cases,
            kind=v2_recall_eval.KIND,
            levels=[run.effort],
            model=run.model,
            max_output_tokens=output_cap,
        )
    raise ValueError(f"{run.label}: unsupported evidence provider {run.provider!r}")


def validate_frozen_case_bindings(
    runs: Sequence[EvidenceRun],
    reviewed_cases: Sequence[dict[str, Any]],
) -> dict[str, dict[str, FrozenCaseBinding]]:
    """Rebuild every provider request from the exact hydrated trusted pack."""
    expected_pack = v2_recall_eval.stage2_pack_fingerprint(reviewed_cases)
    bindings_by_run: dict[str, dict[str, FrozenCaseBinding]] = {}
    for run in runs:
        prefix = f"{run.label} frozen-case binding"
        if run.stage2_pack_fingerprint != expected_pack:
            raise ValueError(
                f"{prefix}: Stage 2 pack fingerprint does not match trusted cases"
            )
        prepared_calls = _prepared_calls_for_run(run, reviewed_cases)
        expected_request_fingerprints = {call.fingerprint for call in prepared_calls}
        if len(expected_request_fingerprints) != len(prepared_calls):
            raise ValueError(f"{prefix}: trusted cases produce duplicate requests")
        if set(run.invocations) != expected_request_fingerprints:
            missing = sorted(expected_request_fingerprints - set(run.invocations))
            extra = sorted(set(run.invocations) - expected_request_fingerprints)
            raise ValueError(
                f"{prefix}: manifest invocation fingerprints do not match rebuilt "
                f"requests; missing={missing}, extra={extra}"
            )

        bindings: dict[str, FrozenCaseBinding] = {}
        for prepared in prepared_calls:
            invocation = run.invocations[prepared.fingerprint]
            expected_qualification = v2_recall_eval.deployment_fingerprint(
                prepared.completion
            )
            if (
                invocation.case != prepared.case_name
                or invocation.effort != prepared.effort
                or invocation.qualification_fingerprint != expected_qualification
            ):
                raise ValueError(
                    f"{prefix}/{prepared.case_name}: manifest invocation identity "
                    "does not match the rebuilt trusted request"
                )
            semantic = v2_recall_eval.semantic_fingerprint(
                prepared.case, prepared.completion
            )
            if semantic in bindings:
                raise ValueError(f"{prefix}: trusted cases produce duplicate semantics")
            outcome = run.outcomes.get(semantic)
            if outcome is None:
                raise ValueError(
                    f"{prefix}/{prepared.case_name}: evidence semantic fingerprint "
                    "does not match the rebuilt trusted request"
                )
            if (
                outcome.case != prepared.case_name
                or outcome.expected_recall != prepared.case.get("expected_recall")
                or outcome.expected_flow != prepared.case.get("expected_flow")
                or outcome.record.get("fingerprint") != prepared.fingerprint
                or outcome.record.get("qualification_fingerprint")
                != expected_qualification
            ):
                raise ValueError(
                    f"{prefix}/{prepared.case_name}: evidence identity or expected "
                    "mapping does not match the rebuilt trusted request"
                )
            if outcome.result is not None:
                stored_result = outcome.result
                provider_payload = {
                    "recall_score": stored_result.recall,
                    "feedback": stored_result.feedback,
                    "follow_up_question": stored_result.follow_up_question,
                    "needs_more_evidence": stored_result.needs_more_evidence,
                    "mastery_summary": stored_result.mastery_summary,
                }
                try:
                    parsed = v2_recall_eval.parse_result(
                        prepared,
                        provider_payload,
                        stored_result.usage,
                    )
                except (llm.LLMError, ValueError) as exc:
                    raise ValueError(
                        f"{prefix}/{prepared.case_name}: stored result no longer "
                        "satisfies the strict V2 contract for its trusted transcript"
                    ) from exc
                if (
                    parsed.recall != stored_result.recall
                    or parsed.flow != stored_result.flow
                    or parsed.feedback != stored_result.feedback
                    or parsed.follow_up_question
                    != stored_result.follow_up_question
                    or parsed.needs_more_evidence
                    is not stored_result.needs_more_evidence
                    or parsed.mastery_summary != stored_result.mastery_summary
                ):
                    raise ValueError(
                        f"{prefix}/{prepared.case_name}: stored flow/text/insufficiency/"
                        "Recall does not exactly match the strict V2 parser"
                    )
            bindings[semantic] = FrozenCaseBinding(
                case=prepared.case,
                prepared=prepared,
                semantic_fingerprint=semantic,
            )
        if set(bindings) != set(run.outcomes):
            extra_cases = sorted(
                outcome.case
                for semantic, outcome in run.outcomes.items()
                if semantic not in bindings
            )
            raise ValueError(
                f"{prefix}: evidence contains cases outside the trusted pack: "
                f"{extra_cases}"
            )
        bindings_by_run[run.evaluation_run_id] = bindings
    return bindings_by_run


def load_text_quality_attestation(path: Path) -> TextQualityAttestation:
    """Load one fail-closed, human-authored Luna text review."""
    raw = path.read_bytes()
    try:
        value = json.loads(
            raw.decode("utf-8"), object_pairs_hook=_object_without_duplicate_keys
        )
    except json.JSONDecodeError as exc:
        raise ValueError(f"{path}: invalid JSON") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{path}: review must be an object")
    required_keys = {
        "format_version",
        "kind",
        "review_method",
        "status",
        "evaluation_run_id",
        "evidence_sha256",
        "manifest_sha256",
        "stage2_pack_fingerprint",
        "model",
        "reviewer",
        "reviewed_at",
        "notes",
        "case_reviews",
    }
    if set(value) != required_keys:
        raise ValueError(
            f"{path}: fields must be exactly {sorted(required_keys)}"
        )
    if value["format_version"] != TEXT_QUALITY_REVIEW_FORMAT_VERSION:
        raise ValueError(f"{path}: unsupported text-quality review format")
    if value["kind"] != TEXT_QUALITY_REVIEW_KIND:
        raise ValueError(f"{path}: unexpected text-quality review kind")
    if value["review_method"] != "human":
        raise ValueError(f"{path}: review_method must be exactly 'human'")
    if value["status"] != "approved":
        raise ValueError(f"{path}: text-quality review is not approved")
    try:
        run_id = str(uuid.UUID(str(value["evaluation_run_id"])))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{path}: invalid evaluation_run_id") from exc
    model = _nonempty_text(value["model"], location=f"{path}: model")
    reviewer = _nonempty_text(value["reviewer"], location=f"{path}: reviewer")
    reviewed_at = _reviewed_at(
        value["reviewed_at"], location=f"{path}: reviewed_at"
    )
    notes = _nonempty_text(value["notes"], location=f"{path}: notes")

    raw_case_reviews = value["case_reviews"]
    if not isinstance(raw_case_reviews, list):
        raise ValueError(f"{path}: case_reviews must be a list")
    case_required_keys = {
        "case",
        "semantic_fingerprint",
        "provider_response_id",
        "feedback_sha256",
        "mastery_summary_sha256",
        "prepared_request_fingerprint",
        "trusted_case_sha256",
        "trusted_case",
        "recall",
        "flow",
        "feedback",
        "mastery_summary",
        "status",
        "notes",
        *TEXT_QUALITY_REVIEW_CHECKS,
    }
    case_reviews: dict[str, TextQualityCaseReview] = {}
    case_names: set[str] = set()
    response_ids: set[str] = set()
    for index, raw_review in enumerate(raw_case_reviews, 1):
        location = f"{path}: case review {index}"
        if not isinstance(raw_review, dict) or set(raw_review) != case_required_keys:
            raise ValueError(
                f"{location}: fields must be exactly {sorted(case_required_keys)}"
            )
        if raw_review["status"] != "approved":
            raise ValueError(f"{location}: review is not approved")
        for check in TEXT_QUALITY_REVIEW_CHECKS:
            if type(raw_review[check]) is not bool:
                raise ValueError(f"{location}: {check} must be boolean")
        for check in TEXT_QUALITY_UNCONDITIONAL_CHECKS:
            if raw_review[check] is not True:
                raise ValueError(f"{location}: {check} must be explicitly true")
        case = _nonempty_text(raw_review["case"], location=f"{location}: case")
        semantic = _semantic_digest(
            raw_review["semantic_fingerprint"], location=location
        )
        response_id = _nonempty_text(
            raw_review["provider_response_id"],
            location=f"{location}: provider_response_id",
        )
        if semantic in case_reviews:
            raise ValueError(f"{location}: duplicate semantic fingerprint")
        if case in case_names:
            raise ValueError(f"{location}: duplicate case")
        if response_id in response_ids:
            raise ValueError(f"{location}: duplicate provider response ID")
        case_names.add(case)
        response_ids.add(response_id)
        trusted_case = raw_review["trusted_case"]
        if not isinstance(trusted_case, dict):
            raise ValueError(f"{location}: trusted_case must be an object")
        trusted_case_sha256 = _sha256_digest(
            raw_review["trusted_case_sha256"],
            location=f"{location}: trusted_case_sha256",
        )
        if trusted_case_sha256 != _canonical_sha256(trusted_case):
            raise ValueError(f"{location}: trusted_case SHA-256 does not match")
        recall = raw_review["recall"]
        if type(recall) is not int or recall not in range(6):
            raise ValueError(f"{location}: recall must be an integer from 0 to 5")
        flow = raw_review["flow"]
        if flow not in v2_recall_eval.VALID_FLOWS:
            raise ValueError(f"{location}: flow is invalid")
        feedback = _nonempty_text(
            raw_review["feedback"], location=f"{location}: feedback"
        )
        mastery_summary = _nonempty_text(
            raw_review["mastery_summary"],
            location=f"{location}: mastery_summary",
        )
        feedback_sha256 = _sha256_digest(
            raw_review["feedback_sha256"],
            location=f"{location}: feedback_sha256",
        )
        mastery_summary_sha256 = _sha256_digest(
            raw_review["mastery_summary_sha256"],
            location=f"{location}: mastery_summary_sha256",
        )
        if feedback_sha256 != _text_sha256(feedback):
            raise ValueError(f"{location}: feedback SHA-256 does not match")
        if mastery_summary_sha256 != _text_sha256(mastery_summary):
            raise ValueError(f"{location}: mastery_summary SHA-256 does not match")
        case_reviews[semantic] = TextQualityCaseReview(
            case=case,
            semantic_fingerprint=semantic,
            provider_response_id=response_id,
            feedback_sha256=feedback_sha256,
            mastery_summary_sha256=mastery_summary_sha256,
            prepared_request_fingerprint=_sha256_digest(
                raw_review["prepared_request_fingerprint"],
                location=f"{location}: prepared_request_fingerprint",
            ),
            trusted_case_sha256=trusted_case_sha256,
            trusted_case=trusted_case,
            recall=recall,
            flow=flow,
            feedback=feedback,
            mastery_summary=mastery_summary,
            notes=_nonempty_text(
                raw_review["notes"], location=f"{location}: notes"
            ),
            checks={check: raw_review[check] for check in TEXT_QUALITY_REVIEW_CHECKS},
        )
    return TextQualityAttestation(
        path=path,
        evaluation_run_id=run_id,
        evidence_sha256=_sha256_digest(
            value["evidence_sha256"], location=f"{path}: evidence_sha256"
        ),
        manifest_sha256=_sha256_digest(
            value["manifest_sha256"], location=f"{path}: manifest_sha256"
        ),
        stage2_pack_fingerprint=_sha256_digest(
            value["stage2_pack_fingerprint"],
            location=f"{path}: stage2_pack_fingerprint",
        ),
        model=model,
        reviewer=reviewer,
        reviewed_at=reviewed_at,
        notes=notes,
        case_reviews=case_reviews,
    )


def text_quality_review_template(
    run: EvidenceRun,
    reviewed_cases: Sequence[dict[str, Any]],
    *,
    reviewer: str,
) -> dict[str, Any]:
    """Create an unapproved template; a human must review and edit every case."""
    reviewer = _nonempty_text(reviewer, location="reviewer")
    bindings = validate_frozen_case_bindings([run], reviewed_cases)[
        run.evaluation_run_id
    ]
    case_reviews: list[dict[str, Any]] = []
    successful = sorted(
        (outcome for outcome in run.outcomes.values() if outcome.succeeded),
        key=lambda item: item.case,
    )
    for outcome in successful:
        result = outcome.result
        if result is None:  # guarded above; keeps type narrowing explicit
            raise AssertionError("successful result disappeared")
        response_id = outcome.record.get("provider_response_id")
        if not isinstance(response_id, str) or not response_id:
            raise ValueError(f"{run.label}/{outcome.case}: missing response ID")
        binding = bindings[outcome.semantic_fingerprint]
        case_reviews.append(
            {
                "case": outcome.case,
                "semantic_fingerprint": outcome.semantic_fingerprint,
                "provider_response_id": response_id,
                "feedback_sha256": _text_sha256(result.feedback),
                "mastery_summary_sha256": _text_sha256(result.mastery_summary),
                "prepared_request_fingerprint": binding.prepared.fingerprint,
                "trusted_case_sha256": _canonical_sha256(binding.case),
                "trusted_case": binding.case,
                "recall": result.recall,
                "flow": result.flow,
                "feedback": result.feedback,
                "mastery_summary": result.mastery_summary,
                "status": "pending",
                "notes": "",
                **{check: False for check in TEXT_QUALITY_REVIEW_CHECKS},
            }
        )
    return {
        "format_version": TEXT_QUALITY_REVIEW_FORMAT_VERSION,
        "kind": TEXT_QUALITY_REVIEW_KIND,
        "review_method": "human",
        "status": "pending",
        "evaluation_run_id": run.evaluation_run_id,
        "evidence_sha256": run.file_digest,
        "manifest_sha256": run.manifest_digest,
        "stage2_pack_fingerprint": run.stage2_pack_fingerprint,
        "model": run.model,
        "reviewer": reviewer,
        "reviewed_at": "",
        "notes": "",
        "case_reviews": case_reviews,
    }


def validate_text_quality_attestations(
    luna_runs: Sequence[EvidenceRun],
    attestations: Sequence[TextQualityAttestation],
    reviewed_cases: Sequence[dict[str, Any]],
    *,
    as_of: datetime | None = None,
) -> None:
    """Bind exactly one approved all-case review to each immutable Luna artifact."""
    comparison_time = as_of or datetime.now(UTC)
    if comparison_time.tzinfo is None or comparison_time.utcoffset() is None:
        raise ValueError("text-quality comparison time must be timezone-aware")
    comparison_time = comparison_time.astimezone(UTC)
    bindings_by_run = validate_frozen_case_bindings(luna_runs, reviewed_cases)
    if len(attestations) != len(luna_runs):
        raise ValueError(
            "expected exactly one text-quality attestation per Luna run; "
            f"received {len(attestations)} for {len(luna_runs)} runs"
        )
    review_paths = [review.path.resolve() for review in attestations]
    if len(set(review_paths)) != len(review_paths):
        raise ValueError("text-quality attestations must be distinct files")
    reviews_by_run: dict[str, TextQualityAttestation] = {}
    for review in attestations:
        if review.evaluation_run_id in reviews_by_run:
            raise ValueError(
                "multiple text-quality attestations target evaluation run "
                f"{review.evaluation_run_id}"
            )
        reviews_by_run[review.evaluation_run_id] = review
    expected_run_ids = {run.evaluation_run_id for run in luna_runs}
    observed_run_ids = set(reviews_by_run)
    if observed_run_ids != expected_run_ids:
        raise ValueError(
            "text-quality attestation run mismatch; "
            f"missing={sorted(expected_run_ids - observed_run_ids)}, "
            f"extra={sorted(observed_run_ids - expected_run_ids)}"
        )

    for run in luna_runs:
        review = reviews_by_run[run.evaluation_run_id]
        prefix = f"{run.label} text-quality review"
        if review.evidence_sha256 != run.file_digest:
            raise ValueError(f"{prefix}: stale or tampered evidence SHA-256")
        if review.manifest_sha256 != run.manifest_digest:
            raise ValueError(f"{prefix}: stale or tampered manifest SHA-256")
        if review.stage2_pack_fingerprint != run.stage2_pack_fingerprint:
            raise ValueError(f"{prefix}: trusted Stage 2 pack does not match evidence")
        if review.model != run.model:
            raise ValueError(f"{prefix}: model does not match evidence")
        if review.reviewed_at < run.created_at:
            raise ValueError(f"{prefix}: review predates its evidence run")
        if review.reviewed_at >= run.qualification_expires_at:
            raise ValueError(f"{prefix}: review is at or after qualification expiry")
        if review.reviewed_at > comparison_time:
            raise ValueError(f"{prefix}: review timestamp is in the future")
        successful = {
            semantic: outcome
            for semantic, outcome in run.outcomes.items()
            if outcome.succeeded
        }
        reviewed = set(review.case_reviews)
        expected = set(successful)
        if reviewed != expected:
            missing = sorted(successful[key].case for key in expected - reviewed)
            extra = sorted(review.case_reviews[key].case for key in reviewed - expected)
            raise ValueError(
                f"{prefix}: every successful case must be reviewed exactly once; "
                f"missing={missing}, extra={extra}"
            )
        for semantic, outcome in successful.items():
            result = outcome.result
            if result is None:  # guarded by succeeded
                raise AssertionError("successful result disappeared")
            if not result.feedback or not result.mastery_summary:
                raise ValueError(
                    f"{prefix}/{outcome.case}: adopted feedback and mastery summary "
                    "must both be non-empty"
                )
            case_review = review.case_reviews[semantic]
            binding = bindings_by_run[run.evaluation_run_id][semantic]
            score_checks = {
                "low_recall_feedback_states_correct_essential_account": (
                    result.recall <= 2
                ),
                "passing_feedback_is_appropriately_direct": result.recall >= 3,
            }
            for check, expected_value in score_checks.items():
                if case_review.checks[check] is not expected_value:
                    applicability = (
                        "true for this score"
                        if expected_value
                        else "false because it is not applicable to this score"
                    )
                    raise ValueError(
                        f"{prefix}/{outcome.case}: {check} must be {applicability} "
                        f"(Recall {result.recall})"
                    )
            if (
                case_review.prepared_request_fingerprint
                != binding.prepared.fingerprint
                or case_review.trusted_case_sha256
                != _canonical_sha256(binding.case)
                or case_review.trusted_case != binding.case
            ):
                raise ValueError(
                    f"{prefix}/{outcome.case}: rendered trusted case or prepared "
                    "request does not match the frozen pack"
                )
            if (
                case_review.recall != result.recall
                or case_review.flow != result.flow
                or case_review.feedback != result.feedback
                or case_review.mastery_summary != result.mastery_summary
            ):
                raise ValueError(
                    f"{prefix}/{outcome.case}: rendered model result does not match "
                    "the immutable evidence row"
                )
            response_id = outcome.record.get("provider_response_id")
            if (
                case_review.case != outcome.case
                or case_review.semantic_fingerprint != outcome.semantic_fingerprint
                or case_review.provider_response_id != response_id
            ):
                raise ValueError(
                    f"{prefix}/{outcome.case}: case/semantic/response identity mismatch"
                )
            if case_review.feedback_sha256 != _text_sha256(result.feedback):
                raise ValueError(
                    f"{prefix}/{outcome.case}: feedback hash is stale or tampered"
                )
            if case_review.mastery_summary_sha256 != _text_sha256(
                result.mastery_summary
            ):
                raise ValueError(
                    f"{prefix}/{outcome.case}: mastery-summary hash is stale or tampered"
                )


def validate_alignment(
    claude: EvidenceRun,
    luna_runs: Sequence[EvidenceRun],
    *,
    expected_claude_model: str,
    expected_claude_effort: str,
    expected_luna_qualification_fingerprint: str,
    expected_stage2_pack_fingerprint: str,
    expected_qualification_expires_at: datetime | None = None,
    as_of: datetime | None = None,
) -> None:
    if len(luna_runs) != 3:
        raise ValueError(f"expected exactly three Luna files, received {len(luna_runs)}")
    paths = [run.path.resolve() for run in (claude, *luna_runs)]
    if len(set(paths)) != 4:
        raise ValueError("Claude and Luna inputs must be four distinct files")
    all_runs = (claude, *luna_runs)
    expiries = {run.qualification_expires_at for run in all_runs}
    if len(expiries) != 1:
        raise ValueError("qualification artifacts use different expiry timestamps")
    qualification_expires_at = next(iter(expiries))
    if (
        expected_qualification_expires_at is not None
        and qualification_expires_at != expected_qualification_expires_at
    ):
        raise ValueError(
            "qualification expiry mismatch: "
            f"expected {expected_qualification_expires_at.isoformat()}, "
            f"observed {qualification_expires_at.isoformat()}"
        )
    if as_of is not None:
        if as_of.tzinfo is None or as_of.utcoffset() is None:
            raise ValueError("comparison time must be timezone-aware")
        comparison_time = as_of.astimezone(UTC)
        future_runs = [run.label for run in all_runs if run.created_at > comparison_time]
        if future_runs:
            raise ValueError(
                "qualification artifacts have future creation timestamps: "
                + ", ".join(future_runs)
            )
        if comparison_time >= qualification_expires_at:
            raise ValueError("qualification evidence has expired")
    if claude.model != expected_claude_model:
        raise ValueError(
            "Claude baseline model mismatch: "
            f"expected {expected_claude_model!r}, observed {claude.model!r}"
        )
    if claude.effort != expected_claude_effort:
        raise ValueError(
            "Claude baseline effort mismatch: "
            f"expected {expected_claude_effort!r}, observed {claude.effort!r}"
        )

    pack_fingerprints = {
        run.stage2_pack_fingerprint for run in (claude, *luna_runs)
    }
    if pack_fingerprints != {expected_stage2_pack_fingerprint}:
        raise ValueError(
            "Stage 2 pack fingerprint mismatch: "
            f"expected {expected_stage2_pack_fingerprint}, "
            f"observed {sorted(pack_fingerprints)}"
        )
    run_ids = {run.evaluation_run_id for run in (claude, *luna_runs)}
    if len(run_ids) != 4:
        raise ValueError("Claude and Luna evidence must come from four fresh runs")
    luna_efforts = {run.effort for run in luna_runs}
    if len(luna_efforts) != 1:
        raise ValueError(f"Luna replicas use mixed effort values: {luna_efforts}")
    luna_qualifications = {
        run.qualification_fingerprint for run in luna_runs
    }
    if luna_qualifications != {expected_luna_qualification_fingerprint}:
        raise ValueError(
            "Luna qualification fingerprint mismatch: "
            f"expected {expected_luna_qualification_fingerprint}, "
            f"observed {sorted(luna_qualifications)}"
        )

    luna_file_digests = [run.file_digest for run in luna_runs]
    if len(set(luna_file_digests)) != 3:
        raise ValueError("duplicate Luna replica files are not fresh evidence")
    for left_index, left in enumerate(luna_runs):
        for right in luna_runs[left_index + 1 :]:
            shared_records = left.record_digests & right.record_digests
            if shared_records:
                raise ValueError(
                    f"{left.label} and {right.label} contain copied replica rows"
                )
            shared_response_ids = left.response_ids & right.response_ids
            if shared_response_ids:
                raise ValueError(
                    f"{left.label} and {right.label} reuse provider response IDs"
                )

    reference_by_case = {
        outcome.case: semantic for semantic, outcome in claude.outcomes.items()
    }
    for run in luna_runs:
        by_case = {outcome.case: semantic for semantic, outcome in run.outcomes.items()}
        missing_cases = sorted(set(reference_by_case) - set(by_case))
        extra_cases = sorted(set(by_case) - set(reference_by_case))
        if missing_cases or extra_cases:
            raise ValueError(
                f"{run.label}: case alignment mismatch; missing={missing_cases}, "
                f"extra={extra_cases}"
            )
        for case, reference_semantic in reference_by_case.items():
            if by_case[case] != reference_semantic:
                raise ValueError(
                    f"{run.label}/{case}: semantic fingerprint mismatch "
                    f"({by_case[case][:12]} != {reference_semantic[:12]})"
                )

        missing = sorted(set(claude.outcomes) - set(run.outcomes))
        extra = sorted(set(run.outcomes) - set(claude.outcomes))
        if missing or extra:
            raise ValueError(
                f"{run.label}: semantic alignment mismatch; missing={missing}, extra={extra}"
            )
        for semantic, reference in claude.outcomes.items():
            candidate = run.outcomes[semantic]
            if (
                candidate.case != reference.case
                or candidate.expected_recall != reference.expected_recall
                or candidate.expected_flow != reference.expected_flow
            ):
                raise ValueError(
                    f"{run.label}/{reference.case}: payload mismatch for semantic "
                    f"fingerprint {semantic[:12]}"
                )


def _metrics(
    label: str,
    outcomes: Sequence[Outcome],
    *,
    provider: str,
    claude_rates: ClaudeRates,
    luna_rates: LunaRates,
) -> RunMetrics:
    successes = [outcome for outcome in outcomes if outcome.result is not None]
    results = [outcome.result for outcome in successes if outcome.result is not None]
    invalids = sum(outcome.invalid for outcome in outcomes if not outcome.succeeded)
    exact = sum(result.recall == result.expected_recall for result in results)
    within_one = sum(
        abs(result.recall - result.expected_recall) <= 1 for result in results
    )
    behavioral_matches = sum(
        v2_recall_eval.behavioral_decisions(result.flow, result.recall)
        == v2_recall_eval.behavioral_decisions(
            result.expected_flow, result.expected_recall
        )
        for result in results
    )
    input_tokens = sum(outcome.usage.input_tokens for outcome in outcomes)
    output_tokens = sum(outcome.usage.output_tokens for outcome in outcomes)
    cache_read_tokens = sum(
        outcome.usage.cache_read_tokens for outcome in outcomes
    )
    cache_write_tokens = sum(
        outcome.usage.cache_write_tokens for outcome in outcomes
    )
    if provider == "claude":
        cost_usd = (
            Decimal(input_tokens) * claude_rates.input_per_million
            + Decimal(output_tokens) * claude_rates.output_per_million
            + Decimal(cache_read_tokens) * claude_rates.cache_read_per_million
            + Decimal(cache_write_tokens) * claude_rates.cache_write_per_million
        ) / Decimal(1_000_000)
    else:
        uncached_input_tokens = (
            input_tokens - cache_read_tokens - cache_write_tokens
        )
        cost_usd = (
            Decimal(uncached_input_tokens) * luna_rates.input_per_million
            + Decimal(cache_read_tokens) * luna_rates.cached_input_per_million
            + Decimal(cache_write_tokens) * luna_rates.cache_write_per_million
            + Decimal(output_tokens) * luna_rates.output_per_million
        ) / Decimal(1_000_000)
    return RunMetrics(
        label=label,
        attempts=len(outcomes),
        successes=len(successes),
        invalids=invalids,
        failures=len(outcomes) - len(successes),
        exact=exact,
        within_one=within_one,
        behavioral_matches=behavioral_matches,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_read_tokens=cache_read_tokens,
        cache_write_tokens=cache_write_tokens,
        latency_samples=tuple(
            outcome.elapsed_ms
            for outcome in outcomes
            if outcome.elapsed_ms is not None
        ),
        cost_usd=cost_usd,
    )


def _decision_json(flow: str, recall: int) -> str:
    return json.dumps(
        v2_recall_eval.behavioral_decisions(flow, recall),
        separators=(",", ":"),
        sort_keys=True,
    )


def build_report(
    claude: EvidenceRun,
    luna_runs: Sequence[EvidenceRun],
    luna_text_reviews: Sequence[TextQualityAttestation],
    reviewed_cases: Sequence[dict[str, Any]],
    *,
    claude_input_price: Decimal,
    claude_output_price: Decimal,
    luna_input_price: Decimal,
    luna_output_price: Decimal,
    claude_cache_read_price: Decimal,
    claude_cache_write_price: Decimal,
    luna_cached_input_price: Decimal,
    luna_cache_write_price: Decimal,
    expected_claude_model: str,
    expected_claude_effort: str,
    expected_luna_qualification_fingerprint: str,
    expected_stage2_pack_fingerprint: str,
    qualification_expires_at: datetime,
    as_of: datetime | None = None,
) -> ComparisonReport:
    comparison_time = as_of or datetime.now(UTC)
    validate_alignment(
        claude,
        luna_runs,
        expected_claude_model=expected_claude_model,
        expected_claude_effort=expected_claude_effort,
        expected_luna_qualification_fingerprint=(
            expected_luna_qualification_fingerprint
        ),
        expected_stage2_pack_fingerprint=expected_stage2_pack_fingerprint,
        expected_qualification_expires_at=qualification_expires_at,
        as_of=comparison_time,
    )
    validate_frozen_case_bindings((claude, *luna_runs), reviewed_cases)
    validate_text_quality_attestations(
        luna_runs,
        luna_text_reviews,
        reviewed_cases,
        as_of=comparison_time,
    )
    expected_claude_rates = {
        "input": claude_input_price,
        "output": claude_output_price,
        "cached_input": claude_cache_read_price,
        "cache_write": claude_cache_write_price,
    }
    if claude.preflight.rates_per_million_usd != expected_claude_rates:
        raise ValueError(
            "Claude comparison rates differ from its approved run manifest"
        )
    expected_luna_rates = {
        "input": luna_input_price,
        "output": luna_output_price,
        "cached_input": luna_cached_input_price,
        "cache_write": luna_cache_write_price,
    }
    for run in luna_runs:
        if run.preflight.rates_per_million_usd != expected_luna_rates:
            raise ValueError(
                f"{run.label}: comparison rates differ from its approved run manifest"
            )
    claude_rates = ClaudeRates(
        claude_input_price,
        claude_output_price,
        claude_cache_read_price,
        claude_cache_write_price,
    )
    luna_rates = LunaRates(
        luna_input_price,
        luna_output_price,
        luna_cached_input_price,
        luna_cache_write_price,
    )
    all_runs = (claude, *luna_runs)
    metrics = tuple(
        _metrics(
            run.label,
            list(run.outcomes.values()),
            provider="claude" if run is claude else "luna",
            claude_rates=claude_rates,
            luna_rates=luna_rates,
        )
        for run in all_runs
    )
    luna_outcomes = [
        outcome for run in luna_runs for outcome in run.outcomes.values()
    ]
    luna_aggregate = _metrics(
        "luna-all-3",
        luna_outcomes,
        provider="luna",
        claude_rates=claude_rates,
        luna_rates=luna_rates,
    )

    confusion: dict[str, Counter[tuple[str, str]]] = {}
    human_disagreements: list[DecisionDisagreement] = []
    for run in all_runs:
        matrix: Counter[tuple[str, str]] = Counter()
        for outcome in run.outcomes.values():
            if outcome.result is None:
                continue
            result = outcome.result
            expected = v2_recall_eval.behavioral_decisions(
                result.expected_flow, result.expected_recall
            )
            actual = v2_recall_eval.behavioral_decisions(result.flow, result.recall)
            matrix[
                (
                    _decision_json(result.expected_flow, result.expected_recall),
                    _decision_json(result.flow, result.recall),
                )
            ] += 1
            if expected != actual:
                human_disagreements.append(
                    DecisionDisagreement(run.label, result.case, expected, actual)
                )
        confusion[run.label] = matrix

    replica_disagreements: list[ReplicaDisagreement] = []
    for run in luna_runs:
        for semantic, claude_outcome in claude.outcomes.items():
            luna_outcome = run.outcomes[semantic]
            if claude_outcome.result is None or luna_outcome.result is None:
                continue
            claude_result = claude_outcome.result
            luna_result = luna_outcome.result
            claude_decisions = v2_recall_eval.behavioral_decisions(
                claude_result.flow, claude_result.recall
            )
            luna_decisions = v2_recall_eval.behavioral_decisions(
                luna_result.flow, luna_result.recall
            )
            if claude_decisions != luna_decisions:
                replica_disagreements.append(
                    ReplicaDisagreement(
                        run.label,
                        claude_result.case,
                        claude_decisions,
                        luna_decisions,
                    )
                )

    gate_failures = v2_recall_eval.qualification_gate_failures(
        {run.label: run.results for run in all_runs}
    )
    gate_failures.extend(
        v2_recall_eval.three_run_stability_failures(
            [run.results for run in luna_runs]
        )
    )
    for run in all_runs:
        for outcome in run.outcomes.values():
            if not outcome.succeeded:
                gate_failures.append(
                    f"{run.label}/{outcome.case}: encoded {outcome.failure_type} failure"
                )

    claude_cost_per_success = metrics[0].cost_per_success
    luna_cost_per_success = luna_aggregate.cost_per_success
    cost_reduction: Decimal | None = None
    if claude_cost_per_success is None or claude_cost_per_success <= 0:
        gate_failures.append("cost gate cannot use a zero/absent Claude cost per success")
    elif luna_cost_per_success is None:
        gate_failures.append("cost gate cannot use a Luna run with zero successes")
    else:
        cost_reduction = Decimal(1) - (
            luna_cost_per_success / claude_cost_per_success
        )
        if cost_reduction < MIN_COST_REDUCTION:
            gate_failures.append(
                f"Luna cost/success reduction {cost_reduction:.2%} is below "
                f"the required {MIN_COST_REDUCTION:.0%}"
            )

    return ComparisonReport(
        metrics=metrics,
        luna_aggregate=luna_aggregate,
        decision_confusion=confusion,
        human_disagreements=tuple(human_disagreements),
        replica_disagreements=tuple(replica_disagreements),
        text_quality_review_count=len(luna_text_reviews),
        cost_reduction=cost_reduction,
        qualification_expires_at=qualification_expires_at,
        gate_failures=tuple(gate_failures),
    )


def _rate_text(numerator: int, denominator: int) -> str:
    if denominator == 0:
        return "n/a"
    return f"{numerator}/{denominator} ({numerator / denominator:.1%})"


def _percentile_95(values: tuple[float, ...]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[max(0, math.ceil(len(ordered) * 0.95) - 1)]


def print_report(report: ComparisonReport) -> None:
    print("=== V2 Recall provider comparison ===")
    print(
        "Qualification expires: "
        + report.qualification_expires_at.astimezone(UTC).isoformat().replace(
            "+00:00", "Z"
        )
    )
    print(
        "Human text-quality gate: "
        f"{report.text_quality_review_count}/3 Luna artifacts approved"
    )
    for metric in (*report.metrics, report.luna_aggregate):
        success_denominator = metric.successes
        invalid_rate = _rate_text(metric.invalids, metric.attempts)
        failure_rate = _rate_text(metric.failures, metric.attempts)
        cost_per_success = metric.cost_per_success
        cost_text = "n/a" if cost_per_success is None else f"${cost_per_success:.6f}"
        if metric.latency_samples:
            average_latency = sum(metric.latency_samples) / len(metric.latency_samples)
            p95_latency = _percentile_95(metric.latency_samples)
            latency_text = f"avg {average_latency:.0f} ms · p95 {p95_latency:.0f} ms"
        else:
            latency_text = "not encoded"
        print(f"\n{metric.label}")
        print(
            f"  exact {_rate_text(metric.exact, success_denominator)} · "
            f"within-one {_rate_text(metric.within_one, success_denominator)} · "
            f"behavior {_rate_text(metric.behavioral_matches, success_denominator)}"
        )
        print(f"  invalid {invalid_rate} · failure {failure_rate}")
        print(
            f"  tokens {metric.input_tokens} input · {metric.output_tokens} output · "
            f"{metric.cache_read_tokens} cache-read · "
            f"{metric.cache_write_tokens} cache-write"
        )
        print(f"  latency {latency_text}")
        print(f"  cost ${metric.cost_usd:.6f} · cost/success {cost_text}")

    print("\n=== full production-decision confusion ===")
    for label, matrix in report.decision_confusion.items():
        print(f"{label}")
        if not matrix:
            print("  no successful results")
        for (expected, actual), count in sorted(matrix.items()):
            print(f"  {count} × expected {expected} -> actual {actual}")

    print("\n=== production-decision disagreements ===")
    if not report.human_disagreements and not report.replica_disagreements:
        print("none")
    for disagreement in report.human_disagreements:
        print(
            f"  human/{disagreement.label}/{disagreement.case}: "
            f"{disagreement.expected!r} -> {disagreement.actual!r}"
        )
    for disagreement in report.replica_disagreements:
        print(
            f"  claude/{disagreement.label}/{disagreement.case}: "
            f"{disagreement.claude!r} -> {disagreement.luna!r}"
        )

    if report.cost_reduction is None:
        print("\nLuna cost/success reduction: unavailable")
    else:
        print(f"\nLuna cost/success reduction: {report.cost_reduction:.2%}")
    print(f"Qualification: {'PASS' if report.passed else 'FAIL'}")
    if report.gate_failures:
        print("\nGate failures:", file=sys.stderr)
        for failure in report.gate_failures:
            print(f"  - {failure}", file=sys.stderr)


def parser() -> argparse.ArgumentParser:
    argument_parser = argparse.ArgumentParser(description=__doc__)
    argument_parser.add_argument(
        "--claude", required=True, type=Path, help="one Claude V2 Recall JSONL"
    )
    argument_parser.add_argument(
        "--luna",
        required=True,
        action="append",
        type=Path,
        help="one fresh Luna V2 Recall JSONL; pass exactly three times",
    )
    argument_parser.add_argument(
        "--luna-text-review",
        required=True,
        action="append",
        type=Path,
        help=(
            "one approved human text-quality review; pass exactly three times "
            "(matching is by evaluation_run_id, not argument order)"
        ),
    )
    argument_parser.add_argument(
        "--claude-input-price-per-million", required=True, type=positive_decimal
    )
    argument_parser.add_argument(
        "--claude-output-price-per-million", required=True, type=positive_decimal
    )
    argument_parser.add_argument(
        "--claude-cache-read-price-per-million",
        required=True,
        type=positive_decimal,
    )
    argument_parser.add_argument(
        "--claude-cache-write-price-per-million",
        required=True,
        type=positive_decimal,
    )
    argument_parser.add_argument(
        "--luna-input-price-per-million", required=True, type=positive_decimal
    )
    argument_parser.add_argument(
        "--luna-output-price-per-million", required=True, type=positive_decimal
    )
    argument_parser.add_argument(
        "--luna-cached-input-price-per-million",
        required=True,
        type=positive_decimal,
    )
    argument_parser.add_argument(
        "--luna-cache-write-price-per-million",
        required=True,
        type=positive_decimal,
    )
    argument_parser.add_argument(
        "--expected-luna-qualification-fingerprint",
        required=True,
        help="exact qualified lowercase SHA-256 deployed for all Luna trials",
    )
    argument_parser.add_argument(
        "--qualification-expires-at",
        required=True,
        type=qualification_expiry_arg,
        help=(
            "exact UTC deadline frozen into all four fresh manifests; must be "
            "unexpired and no more than 30 days after each run"
        ),
    )
    argument_parser.add_argument(
        "--expected-claude-model",
        required=True,
        help="exact stabilized production Claude model used for the baseline",
    )
    argument_parser.add_argument(
        "--expected-claude-effort",
        required=True,
        help="exact stabilized production Claude effort used for the baseline",
    )
    argument_parser.add_argument(
        "--cases",
        required=True,
        type=Path,
        help="the exact frozen human-reviewed V2 Recall pack",
    )
    argument_parser.add_argument(
        "--grounding-manifest",
        required=True,
        type=Path,
        help="approved cards manifest used to hydrate the frozen pack",
    )
    return argument_parser


def main(argv: Sequence[str] | None = None) -> int:
    argument_parser = parser()
    args = argument_parser.parse_args(argv)
    if len(args.luna) != 3:
        argument_parser.error(
            f"--luna must be passed exactly three times; received {len(args.luna)}"
        )
    if len(args.luna_text_review) != 3:
        argument_parser.error(
            "--luna-text-review must be passed exactly three times; "
            f"received {len(args.luna_text_review)}"
        )
    if not args.expected_claude_model.strip():
        argument_parser.error("--expected-claude-model must be non-empty")
    if not args.expected_claude_effort.strip():
        argument_parser.error("--expected-claude-effort must be non-empty")
    try:
        expected_qualification = _semantic_digest(
            args.expected_luna_qualification_fingerprint,
            location="--expected-luna-qualification-fingerprint",
        )
    except ValueError as exc:
        argument_parser.error(str(exc))
    try:
        reviewed_cases = hydrate_grounding(
            load_cases(args.cases, argument_parser),
            args.grounding_manifest,
            argument_parser,
        )
        pack_failures = v2_recall_eval.stage2_pack_failures(reviewed_cases)
        if pack_failures:
            argument_parser.error(
                "Stage 2 pack failed validation: " + "; ".join(pack_failures)
            )
        expected_pack_fingerprint = v2_recall_eval.stage2_pack_fingerprint(
            reviewed_cases
        )
    except OSError as exc:
        argument_parser.error(str(exc))
    try:
        claude = load_evidence(args.claude, label="claude", provider="claude")
        luna_runs = [
            load_evidence(path, label=f"luna-{index}", provider="luna")
            for index, path in enumerate(args.luna, 1)
        ]
        luna_text_reviews = [
            load_text_quality_attestation(path)
            for path in args.luna_text_review
        ]
        report = build_report(
            claude,
            luna_runs,
            luna_text_reviews,
            reviewed_cases,
            claude_input_price=args.claude_input_price_per_million,
            claude_output_price=args.claude_output_price_per_million,
            luna_input_price=args.luna_input_price_per_million,
            luna_output_price=args.luna_output_price_per_million,
            claude_cache_read_price=(
                args.claude_cache_read_price_per_million
            ),
            claude_cache_write_price=(
                args.claude_cache_write_price_per_million
            ),
            luna_cached_input_price=(
                args.luna_cached_input_price_per_million
            ),
            luna_cache_write_price=(
                args.luna_cache_write_price_per_million
            ),
            expected_claude_model=args.expected_claude_model,
            expected_claude_effort=args.expected_claude_effort,
            expected_luna_qualification_fingerprint=expected_qualification,
            expected_stage2_pack_fingerprint=expected_pack_fingerprint,
            qualification_expires_at=args.qualification_expires_at,
        )
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        argument_parser.error(str(exc))
    print_report(report)
    return 0 if report.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
