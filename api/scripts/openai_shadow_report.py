#!/usr/bin/env python
"""Audit the first N exported OpenAI V2 shadow events without DB or network access.

The input is a JSON list (or ``{"rows": [...]}``) of ``llm_usage``-like rows.
Every row must contain the privacy-safe ``details`` object written by scoring.
Pricing is deliberately supplied on every invocation; this script contains no
provider price table that can silently become stale.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import uuid
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import parse_strict_utc_timestamp  # noqa: E402
from app.services.ai_consent import POLICY_VERSION as AI_CONSENT_POLICY_VERSION  # noqa: E402
from app.services.scoring_provider import product_decisions  # noqa: E402
from scripts.effort_sweep_support import (  # noqa: E402
    MIN_OPENAI_V2_COST_REDUCTION as MIN_COST_REDUCTION,
)
from scripts.effort_sweep_support import positive_decimal  # noqa: E402

DEFAULT_EVENT_COUNT = 100
PROVIDERS = ("anthropic", "openai")
ROW_REQUIRED_KEYS = frozenset(
    {"id", "user_id", "operation", "created_at", "details"}
)
ROW_OPTIONAL_KEYS = frozenset()
DETAIL_REQUIRED_KEYS = frozenset(
    {
        "route",
        "authoritative_provider",
        "qualification_fingerprint",
        "qualification_expires_at",
        "fallback_reason",
        "candidate_error",
        "scoring_event_id",
        "event_started_at",
        "session_id",
        "scoring_contract_version",
        "probes_used",
        "ai_consent_policy_version",
        "call",
    }
)
DETAIL_OPTIONAL_KEYS = frozenset(
    {
        "shadow",
        "ai_consent_verified",
        "openai_allowlist_verified",
    }
)
INTENT_DETAIL_KEYS = frozenset(
    {
        "audit_type",
        "manifest_version",
        "status",
        "reserved_calls",
        "finalized_at",
        "terminal_call_count",
        "shadow_stage_id",
        "shadow_stage_ordinal",
        "route",
        "authoritative_provider",
        "qualification_fingerprint",
        "qualification_expires_at",
        "scoring_event_id",
        "event_started_at",
        "session_id",
        "scoring_contract_version",
        "probes_used",
        "ai_consent_policy_version",
        "ai_consent_verified",
        "openai_allowlist_verified",
        "expected_calls",
    }
)
INTENT_COMMON_KEYS = frozenset(
    {
        "route",
        "authoritative_provider",
        "qualification_fingerprint",
        "qualification_expires_at",
        "scoring_event_id",
        "event_started_at",
        "session_id",
        "scoring_contract_version",
        "probes_used",
        "ai_consent_policy_version",
    }
)
EXPECTED_CALL_KEYS = frozenset({"provider", "model", "requirement"})
EVENT_COMMON_KEYS = frozenset(
    {
        "route",
        "authoritative_provider",
        "qualification_fingerprint",
        "qualification_expires_at",
        "fallback_reason",
        "candidate_error",
        "scoring_event_id",
        "event_started_at",
        "session_id",
        "scoring_contract_version",
        "probes_used",
        "ai_consent_policy_version",
    }
)
CALL_KEYS = frozenset(
    {
        "provider",
        "model",
        "response_model",
        "response_id",
        "latency_ms",
        "input_tokens",
        "output_tokens",
        "cached_input_tokens",
        "cache_write_tokens",
        "outcome",
        "error_type",
    }
)
SHADOW_KEYS = frozenset(
    {
        "authoritative_recall",
        "candidate_recall",
        "within_one",
        "behavioral_match",
        "authoritative_decisions",
        "candidate_decisions",
    }
)
SHADOW_FLOWS = frozenset({"follow_up", "complete"})

# Telemetry may describe decisions and usage, never make a second analytics copy
# of learner or model text. Match keys rather than values so the audit itself does
# not need to inspect or print potentially sensitive strings.
FORBIDDEN_KEY_FRAGMENTS = (
    "answer",
    "api_key",
    "authorization",
    "completion",
    "content",
    "feedback",
    "grounding",
    "instructions",
    "mastery",
    "output_text",
    "prompt",
    "question",
    "request_body",
    "response_body",
    "rubric",
    "secret",
    "source_excerpt",
    "topic",
    "transcript",
    "user_content",
)


class ShadowReportError(ValueError):
    """Raised when an export cannot unambiguously support the shadow report."""


@dataclass(frozen=True)
class ProviderRates:
    input_per_million: Decimal
    output_per_million: Decimal
    cached_input_per_million: Decimal
    cache_write_per_million: Decimal


@dataclass(frozen=True)
class ProviderIdentity:
    requested_model: str
    response_model: str


@dataclass(frozen=True)
class AuditRow:
    row_id: str
    created_at: datetime
    event_id: str
    event_started_at: datetime
    details: dict[str, Any]
    call: dict[str, Any]

    @property
    def provider(self) -> str:
        return str(self.call["provider"])


@dataclass(frozen=True)
class IntentRow:
    row_id: str
    created_at: datetime
    event_id: str
    event_started_at: datetime
    details: dict[str, Any]
    expected_calls: dict[str, dict[str, str]]
    shadow_stage_id: str
    shadow_stage_ordinal: int


@dataclass(frozen=True)
class ShadowEvent:
    event_id: str
    event_started_at: datetime
    rows: dict[str, AuditRow]
    shadow: dict[str, Any] | None
    consent_current: bool | None
    allowlist_eligible: bool | None
    qualification_expires_at: str
    shadow_stage_ordinal: int
    audit_gap: str = ""


def positive_int(value: str) -> int:
    parsed = int(value)
    if parsed < 1:
        raise argparse.ArgumentTypeError("must be greater than zero")
    return parsed


def fingerprint_arg(value: str) -> str:
    if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
        raise argparse.ArgumentTypeError(
            "must be an exact 64-character lowercase SHA-256 hex digest"
        )
    return value


def qualification_expiry_arg(value: str) -> str:
    if value != value.strip():
        raise argparse.ArgumentTypeError("must not contain surrounding whitespace")
    try:
        parse_strict_utc_timestamp(
            value,
            field="expected qualification expiry",
        )
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc
    return value


def _normalized_key(value: object) -> str:
    return str(value).strip().lower().replace("-", "_").replace(" ", "_")


def _privacy_failures(value: Any, *, path: str = "details") -> list[str]:
    failures: list[str] = []
    if isinstance(value, dict):
        for raw_key, child in value.items():
            key = _normalized_key(raw_key)
            child_path = f"{path}.{raw_key}"
            if any(fragment in key for fragment in FORBIDDEN_KEY_FRAGMENTS):
                failures.append(child_path)
            failures.extend(_privacy_failures(child, path=child_path))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            failures.extend(_privacy_failures(child, path=f"{path}[{index}]"))
    return failures


def _parse_created_at(value: object, *, row_number: int) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise ShadowReportError(f"row {row_number} has no created_at timestamp")
    text = value.strip()
    try:
        parsed = datetime.fromisoformat(text[:-1] + "+00:00" if text.endswith("Z") else text)
    except ValueError as exc:
        raise ShadowReportError(
            f"row {row_number} has an invalid created_at timestamp: {text!r}"
        ) from exc
    if parsed.tzinfo is None:
        raise ShadowReportError(
            f"row {row_number} created_at must include a timezone offset"
        )
    return parsed


def _parse_event_started_at(
    value: object, *, row_number: int, event_id: str
) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise ShadowReportError(
            f"row {row_number} event {event_id} has no event_started_at timestamp"
        )
    text = value.strip()
    try:
        parsed = datetime.fromisoformat(
            text[:-1] + "+00:00" if text.endswith("Z") else text
        )
    except ValueError as exc:
        raise ShadowReportError(
            f"row {row_number} event {event_id} has an invalid "
            f"event_started_at timestamp: {text!r}"
        ) from exc
    if parsed.tzinfo is None:
        raise ShadowReportError(
            f"row {row_number} event {event_id} event_started_at must include "
            "a timezone offset"
        )
    return parsed


def _nonnegative_int(value: object, *, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ShadowReportError(f"{label} must be a non-negative integer")
    return value


def _required_text(value: object, *, label: str) -> str:
    if type(value) is not str or not value.strip():
        raise ShadowReportError(f"{label} must be non-empty text")
    return value


def _text(value: object, *, label: str) -> str:
    if type(value) is not str:
        raise ShadowReportError(f"{label} must be text")
    return value


def _partition_intents(
    raw_rows: list[Any],
    *,
    expected_models: dict[str, ProviderIdentity],
    expected_user_id: uuid.UUID,
) -> tuple[list[Any], dict[str, IntentRow]]:
    """Separate pre-call manifests while validating their strict safe shape."""
    terminal_rows: list[Any] = []
    intents: dict[str, IntentRow] = {}
    seen_row_ids: set[str] = set()
    for row_number, raw in enumerate(raw_rows, 1):
        if not isinstance(raw, dict):
            raise ShadowReportError(f"row {row_number} is not an object")
        privacy_failures = _privacy_failures(raw, path=f"row[{row_number}]")
        if privacy_failures:
            raise ShadowReportError(
                "content-like telemetry key rejected: " + privacy_failures[0]
            )
        missing_row_keys = ROW_REQUIRED_KEYS - set(raw)
        extra_row_keys = set(raw) - ROW_REQUIRED_KEYS - ROW_OPTIONAL_KEYS
        if missing_row_keys or extra_row_keys:
            raise ShadowReportError(
                f"row {row_number} export shape is invalid: "
                f"missing={sorted(missing_row_keys)}, extra={sorted(extra_row_keys)}"
            )
        raw_id = raw.get("id")
        if isinstance(raw_id, bool) or not isinstance(raw_id, (str, int)):
            raise ShadowReportError(f"row {row_number} has no stable id")
        row_id = str(raw_id).strip()
        if not row_id:
            raise ShadowReportError(f"row {row_number} has no stable id")
        if row_id in seen_row_ids:
            raise ShadowReportError(f"duplicate llm_usage row id: {row_id}")
        seen_row_ids.add(row_id)

        if raw.get("operation") != "score_v2_intent":
            terminal_rows.append(raw)
            continue

        try:
            row_user_id = uuid.UUID(str(raw.get("user_id")))
        except (TypeError, ValueError) as exc:
            raise ShadowReportError(
                f"row {row_number} has an invalid user_id"
            ) from exc
        if row_user_id != expected_user_id:
            raise ShadowReportError(
                f"row {row_number} belongs to a user outside the owner canary"
            )
        created_at = _parse_created_at(raw.get("created_at"), row_number=row_number)
        details = raw.get("details")
        if not isinstance(details, dict) or set(details) != INTENT_DETAIL_KEYS:
            keys = set(details) if isinstance(details, dict) else set()
            raise ShadowReportError(
                f"row {row_number} intent shape is invalid: "
                f"missing={sorted(INTENT_DETAIL_KEYS - keys)}, "
                f"extra={sorted(keys - INTENT_DETAIL_KEYS)}"
            )
        if details.get("audit_type") != "scoring_event_intent":
            raise ShadowReportError(f"row {row_number} has an invalid intent audit_type")
        if details.get("manifest_version") != 1:
            raise ShadowReportError(f"row {row_number} has an invalid manifest_version")
        status = details.get("status")
        if status not in {"pending", "incomplete", "finalized"}:
            raise ShadowReportError(f"row {row_number} has an invalid intent status")
        reserved_calls = _nonnegative_int(
            details.get("reserved_calls"),
            label=f"event intent row {row_number} reserved_calls",
        )
        if reserved_calls < 1:
            raise ShadowReportError(
                f"event intent row {row_number} reserved_calls must be positive"
            )
        terminal_call_count = _nonnegative_int(
            details.get("terminal_call_count"),
            label=f"event intent row {row_number} terminal_call_count",
        )
        finalized_at_value = details.get("finalized_at")
        if status == "pending":
            if finalized_at_value is not None or terminal_call_count != 0:
                raise ShadowReportError(
                    f"row {row_number} pending intent has terminal state"
                )
        elif status == "incomplete":
            if finalized_at_value is not None or not (
                0 < terminal_call_count < reserved_calls
            ):
                raise ShadowReportError(
                    f"row {row_number} incomplete intent has invalid terminal state"
                )
        elif not isinstance(finalized_at_value, str):
            raise ShadowReportError(
                f"row {row_number} finalized intent has no finalized_at"
            )
        else:
            finalized_at = _parse_created_at(
                finalized_at_value, row_number=row_number
            )
            if finalized_at < created_at:
                raise ShadowReportError(
                    f"row {row_number} intent finalized before it was created"
                )
        event_id = _required_text(
            details.get("scoring_event_id"),
            label=f"row {row_number} scoring_event_id",
        ).strip()
        if event_id in intents:
            raise ShadowReportError(f"event {event_id} has duplicate intent rows")
        event_started_at = _parse_event_started_at(
            details.get("event_started_at"),
            row_number=row_number,
            event_id=event_id,
        )
        if created_at < event_started_at:
            raise ShadowReportError(
                f"event {event_id} intent was created before event_started_at"
            )
        _required_text(details.get("session_id"), label=f"event {event_id} session_id")
        _nonnegative_int(
            details.get("probes_used"), label=f"event {event_id} probes_used"
        )
        if details.get("scoring_contract_version") != 2:
            raise ShadowReportError(f"event {event_id} scoring contract must be V2")
        if details.get("ai_consent_policy_version") != AI_CONSENT_POLICY_VERSION:
            raise ShadowReportError(
                f"event {event_id} ai_consent_policy_version must equal the current "
                f"policy version {AI_CONSENT_POLICY_VERSION!r}"
            )
        for field in ("ai_consent_verified", "openai_allowlist_verified"):
            if details.get(field) is not None and type(details.get(field)) is not bool:
                raise ShadowReportError(
                    f"event {event_id} {field} must be boolean or null"
                )
        _required_text(
            details.get("qualification_fingerprint"),
            label=f"event {event_id} qualification_fingerprint",
        )
        try:
            parse_strict_utc_timestamp(
                _required_text(
                    details.get("qualification_expires_at"),
                    label=f"event {event_id} qualification_expires_at",
                ),
                field=f"event {event_id} qualification_expires_at",
            )
        except ValueError as exc:
            raise ShadowReportError(str(exc)) from exc
        if details.get("route") not in {"shadow", "primary"}:
            raise ShadowReportError(f"event {event_id} intent route is invalid")
        shadow_stage_id = _required_text(
            details.get("shadow_stage_id"),
            label=f"event {event_id} shadow_stage_id",
        ).strip()
        try:
            normalized_stage_id = str(uuid.UUID(shadow_stage_id))
        except ValueError as exc:
            raise ShadowReportError(
                f"event {event_id} shadow_stage_id must be a UUID"
            ) from exc
        if normalized_stage_id != shadow_stage_id:
            raise ShadowReportError(
                f"event {event_id} shadow_stage_id must use canonical UUID text"
            )
        shadow_stage_ordinal = _nonnegative_int(
            details.get("shadow_stage_ordinal"),
            label=f"event {event_id} shadow_stage_ordinal",
        )
        if shadow_stage_ordinal < 1:
            raise ShadowReportError(
                f"event {event_id} shadow_stage_ordinal must be positive"
            )

        raw_calls = details.get("expected_calls")
        if not isinstance(raw_calls, list) or not raw_calls:
            raise ShadowReportError(f"event {event_id} has no expected calls")
        expected_calls: dict[str, dict[str, str]] = {}
        for position, call in enumerate(raw_calls, 1):
            if not isinstance(call, dict) or set(call) != EXPECTED_CALL_KEYS:
                raise ShadowReportError(
                    f"event {event_id} expected call {position} shape is invalid"
                )
            provider = call.get("provider")
            if provider not in PROVIDERS or provider in expected_calls:
                raise ShadowReportError(
                    f"event {event_id} expected provider manifest is invalid"
                )
            model = call.get("model")
            if model != expected_models[provider].requested_model:
                raise ShadowReportError(
                    f"event {event_id} {provider} intent model must be "
                    f"{expected_models[provider].requested_model!r}"
                )
            requirement = call.get("requirement")
            if requirement not in {"required", "conditional_fallback"}:
                raise ShadowReportError(
                    f"event {event_id} {provider} intent requirement is invalid"
                )
            expected_calls[provider] = {
                "provider": provider,
                "model": str(model),
                "requirement": str(requirement),
            }
        intents[event_id] = IntentRow(
            row_id=row_id,
            created_at=created_at,
            event_id=event_id,
            event_started_at=event_started_at,
            details=details,
            expected_calls=expected_calls,
            shadow_stage_id=shadow_stage_id,
            shadow_stage_ordinal=shadow_stage_ordinal,
        )
    return terminal_rows, intents


def _parse_rows(
    raw_rows: list[Any],
    *,
    expected_models: dict[str, ProviderIdentity],
    expected_user_id: uuid.UUID,
) -> list[AuditRow]:
    if not raw_rows:
        return []
    rows: list[AuditRow] = []
    seen_row_ids: set[str] = set()
    for row_number, raw in enumerate(raw_rows, 1):
        if not isinstance(raw, dict):
            raise ShadowReportError(f"row {row_number} is not an object")
        privacy_failures = _privacy_failures(raw, path=f"row[{row_number}]")
        if privacy_failures:
            raise ShadowReportError(
                "content-like telemetry key rejected: " + privacy_failures[0]
            )
        missing_row_keys = ROW_REQUIRED_KEYS - set(raw)
        extra_row_keys = set(raw) - ROW_REQUIRED_KEYS - ROW_OPTIONAL_KEYS
        if missing_row_keys or extra_row_keys:
            raise ShadowReportError(
                f"row {row_number} export shape is invalid: "
                f"missing={sorted(missing_row_keys)}, extra={sorted(extra_row_keys)}"
            )
        raw_id = raw.get("id")
        if isinstance(raw_id, bool) or not isinstance(raw_id, (str, int)):
            raise ShadowReportError(f"row {row_number} has no stable id")
        row_id = str(raw_id).strip()
        if not row_id:
            raise ShadowReportError(f"row {row_number} has no stable id")
        if row_id in seen_row_ids:
            raise ShadowReportError(f"duplicate llm_usage row id: {row_id}")
        seen_row_ids.add(row_id)
        try:
            row_user_id = uuid.UUID(str(raw.get("user_id")))
        except (TypeError, ValueError) as exc:
            raise ShadowReportError(
                f"row {row_number} has an invalid user_id"
            ) from exc
        if row_user_id != expected_user_id:
            raise ShadowReportError(
                f"row {row_number} belongs to a user outside the owner canary"
            )
        created_at = _parse_created_at(raw.get("created_at"), row_number=row_number)
        if raw.get("operation") != "score_v2":
            raise ShadowReportError(
                f"row {row_number} is not a score_v2 usage row"
            )
        details = raw.get("details")
        if not isinstance(details, dict):
            raise ShadowReportError(f"row {row_number} details must be an object")
        missing_detail_keys = DETAIL_REQUIRED_KEYS - set(details)
        extra_detail_keys = set(details) - DETAIL_REQUIRED_KEYS - DETAIL_OPTIONAL_KEYS
        if missing_detail_keys or extra_detail_keys:
            raise ShadowReportError(
                f"row {row_number} details shape is invalid: "
                f"missing={sorted(missing_detail_keys)}, extra={sorted(extra_detail_keys)}"
            )
        event_id = details.get("scoring_event_id")
        event_id = _required_text(
            event_id,
            label=f"row {row_number} scoring_event_id",
        )
        event_id = event_id.strip()
        event_started_at = _parse_event_started_at(
            details.get("event_started_at"),
            row_number=row_number,
            event_id=event_id,
        )
        _required_text(
            details.get("session_id"),
            label=f"event {event_id} session_id",
        )
        _nonnegative_int(
            details.get("probes_used"),
            label=f"event {event_id} probes_used",
        )
        _text(
            details.get("fallback_reason"),
            label=f"event {event_id} fallback_reason",
        )
        _text(
            details.get("candidate_error"),
            label=f"event {event_id} candidate_error",
        )
        _required_text(
            details.get("ai_consent_policy_version"),
            label=f"event {event_id} ai_consent_policy_version",
        )
        _required_text(
            details.get("qualification_fingerprint"),
            label=f"event {event_id} qualification_fingerprint",
        )
        try:
            parse_strict_utc_timestamp(
                _required_text(
                    details.get("qualification_expires_at"),
                    label=f"event {event_id} qualification_expires_at",
                ),
                field=f"event {event_id} qualification_expires_at",
            )
        except ValueError as exc:
            raise ShadowReportError(str(exc)) from exc
        if type(details.get("scoring_contract_version")) is not int:
            raise ShadowReportError(
                f"event {event_id} scoring_contract_version must be an integer"
            )
        call = details.get("call")
        if not isinstance(call, dict):
            raise ShadowReportError(f"row {row_number} details.call must be an object")
        if set(call) != CALL_KEYS:
            raise ShadowReportError(
                f"row {row_number} details.call shape is invalid: "
                f"missing={sorted(CALL_KEYS - set(call))}, "
                f"extra={sorted(set(call) - CALL_KEYS)}"
            )
        provider = call.get("provider")
        if provider not in PROVIDERS:
            raise ShadowReportError(
                f"event {event_id} has unsupported provider {provider!r}"
            )
        outcome = call.get("outcome")
        if not isinstance(outcome, str) or not outcome:
            raise ShadowReportError(
                f"event {event_id} {provider} call has no outcome"
            )
        error_type = call.get("error_type", "")
        if not isinstance(error_type, str):
            raise ShadowReportError(
                f"event {event_id} {provider} error_type must be text"
            )
        if outcome != "success" and not error_type:
            raise ShadowReportError(
                f"event {event_id} {provider} non-success call needs a typed error_type"
            )
        if outcome == "success" and error_type:
            raise ShadowReportError(
                f"event {event_id} {provider} successful call cannot have error_type"
            )
        identity = expected_models[provider]
        model = call.get("model")
        response_model = call.get("response_model")
        response_id = call.get("response_id")
        if model != identity.requested_model:
            raise ShadowReportError(
                f"event {event_id} {provider} requested model must be "
                f"{identity.requested_model!r}"
            )
        if not isinstance(response_model, str) or not isinstance(response_id, str):
            raise ShadowReportError(
                f"event {event_id} {provider} response identity must be text"
            )
        if outcome == "success":
            if response_model != identity.response_model:
                raise ShadowReportError(
                    f"event {event_id} {provider} response model must be "
                    f"{identity.response_model!r}"
                )
            if not response_id.strip():
                raise ShadowReportError(
                    f"event {event_id} {provider} successful call needs a response id"
                )
        elif response_model and response_model != identity.response_model:
            raise ShadowReportError(
                f"event {event_id} {provider} failure response model is unrecognized"
            )
        token_values: dict[str, int] = {}
        for field in (
            "latency_ms",
            "input_tokens",
            "output_tokens",
            "cached_input_tokens",
            "cache_write_tokens",
        ):
            token_values[field] = _nonnegative_int(
                call.get(field, 0), label=f"event {event_id} {provider} {field}"
            )
        if outcome == "success" and (
            token_values["input_tokens"] == 0
            or token_values["output_tokens"] == 0
        ):
            raise ShadowReportError(
                f"event {event_id} {provider} successful call requires positive "
                "input and output tokens"
            )
        if provider == "openai" and (
            token_values["cached_input_tokens"]
            + token_values["cache_write_tokens"]
            > token_values["input_tokens"]
        ):
            raise ShadowReportError(
                f"event {event_id} OpenAI cached and cache-write tokens exceed "
                "total input tokens"
            )
        rows.append(
            AuditRow(
                row_id=row_id,
                created_at=created_at,
                event_id=event_id,
                event_started_at=event_started_at,
                details=details,
                call=call,
            )
        )
    return rows


def _shared_event_details(rows: list[AuditRow]) -> dict[str, object]:
    """Attest that both physical-call rows describe the exact same event."""
    event_id = rows[0].event_id
    shared: dict[str, object] = {}
    for field in EVENT_COMMON_KEYS:
        first = rows[0].details[field]
        if any(
            type(row.details[field]) is not type(first)
            or row.details[field] != first
            for row in rows[1:]
        ):
            raise ShadowReportError(
                f"event {event_id} has conflicting {field} values"
            )
        shared[field] = first
    if shared["ai_consent_policy_version"] != AI_CONSENT_POLICY_VERSION:
        raise ShadowReportError(
            f"event {event_id} ai_consent_policy_version must equal the current "
            f"policy version {AI_CONSENT_POLICY_VERSION!r}"
        )
    return shared


def _common_optional_flag(rows: list[AuditRow], field: str) -> bool | None:
    present = [field in row.details for row in rows]
    if any(present) and not all(present):
        raise ShadowReportError(
            f"event {rows[0].event_id} has an ambiguous partial {field} flag"
        )
    if not any(present):
        return None
    values = [row.details[field] for row in rows]
    if any(not isinstance(value, bool) for value in values):
        raise ShadowReportError(
            f"event {rows[0].event_id} {field} must be boolean"
        )
    if len(set(values)) != 1:
        raise ShadowReportError(
            f"event {rows[0].event_id} has conflicting {field} flags"
        )
    return values[0]


def _common_shadow(rows: list[AuditRow]) -> dict[str, Any] | None:
    present = ["shadow" in row.details for row in rows]
    if any(present) and not all(present):
        raise ShadowReportError(
            f"event {rows[0].event_id} has an ambiguous partial shadow comparison"
        )
    if not any(present):
        return None
    values = [row.details["shadow"] for row in rows]
    if any(not isinstance(value, dict) for value in values):
        raise ShadowReportError(
            f"event {rows[0].event_id} shadow comparison must be an object"
        )
    encoded = {json.dumps(value, sort_keys=True, separators=(",", ":")) for value in values}
    if len(encoded) != 1:
        raise ShadowReportError(
            f"event {rows[0].event_id} has conflicting shadow comparisons"
        )
    shadow = values[0]
    if set(shadow) != SHADOW_KEYS:
        missing = sorted(SHADOW_KEYS - set(shadow))
        extra = sorted(set(shadow) - SHADOW_KEYS)
        raise ShadowReportError(
            f"event {rows[0].event_id} shadow comparison shape is invalid: "
            f"missing={missing}, extra={extra}"
        )
    for field in ("within_one", "behavioral_match"):
        if type(shadow[field]) is not bool:
            raise ShadowReportError(
                f"event {rows[0].event_id} shadow.{field} must be boolean"
            )
    for field in ("authoritative_recall", "candidate_recall"):
        value = shadow[field]
        if value is not None and (
            type(value) is not int or value not in range(6)
        ):
            raise ShadowReportError(
                f"event {rows[0].event_id} shadow.{field} is invalid"
            )

    normalized_decisions: dict[str, dict[str, str]] = {}
    for label in ("authoritative", "candidate"):
        field = f"{label}_decisions"
        decisions = shadow[field]
        if not isinstance(decisions, dict):
            raise ShadowReportError(
                f"event {rows[0].event_id} shadow.{field} must be an object"
            )
        flow = decisions.get("flow")
        if flow not in SHADOW_FLOWS:
            raise ShadowReportError(
                f"event {rows[0].event_id} shadow.{field}.flow is invalid"
            )
        recall = shadow[f"{label}_recall"]
        # Successful runtime results always retain the parsed numeric Recall,
        # including the provisional value that accompanies a follow-up. That
        # value has no product effect because `product_decisions` returns only
        # the flow branch, but dropping it would make the audit unlike runtime.
        if recall is None:
            raise ShadowReportError(
                f"event {rows[0].event_id} shadow {label} flow/recall is inconsistent"
            )
        expected = product_decisions(status=flow, recall=recall)
        if decisions != expected:
            raise ShadowReportError(
                f"event {rows[0].event_id} shadow.{field} does not match "
                "its primitive flow/recall"
            )
        normalized_decisions[label] = expected

    authoritative_recall = shadow["authoritative_recall"]
    candidate_recall = shadow["candidate_recall"]
    computed_within_one = (
        authoritative_recall is not None
        and candidate_recall is not None
        and abs(authoritative_recall - candidate_recall) <= 1
    )
    computed_behavioral_match = (
        normalized_decisions["authoritative"]
        == normalized_decisions["candidate"]
    )
    if shadow["within_one"] is not computed_within_one:
        raise ShadowReportError(
            f"event {rows[0].event_id} shadow.within_one disagrees with recalls"
        )
    if shadow["behavioral_match"] is not computed_behavioral_match:
        raise ShadowReportError(
            f"event {rows[0].event_id} shadow.behavioral_match disagrees with decisions"
        )
    return {
        "authoritative_recall": authoritative_recall,
        "candidate_recall": candidate_recall,
        "within_one": computed_within_one,
        "behavioral_match": computed_behavioral_match,
        "authoritative_decisions": normalized_decisions["authoritative"],
        "candidate_decisions": normalized_decisions["candidate"],
    }


def _group_events(
    rows: list[AuditRow],
    intents: dict[str, IntentRow],
    *,
    expected_fingerprint: str,
    expected_stage_id: str,
) -> list[ShadowEvent]:
    grouped: dict[str, list[AuditRow]] = defaultdict(list)
    for row in rows:
        grouped[row.event_id].append(row)

    terminal_without_intent = sorted(set(grouped) - set(intents))
    if terminal_without_intent:
        raise ShadowReportError(
            "terminal scoring rows have no pre-call intent: "
            + ", ".join(terminal_without_intent)
        )
    if not intents:
        raise ShadowReportError("the export contains no scoring-event intents")

    successful_response_ids: dict[tuple[str, str], str] = {}
    for row in rows:
        if row.call.get("outcome") != "success":
            continue
        response_id = str(row.call.get("response_id", ""))
        key = (row.provider, response_id)
        previous_event = successful_response_ids.get(key)
        if previous_event is not None and previous_event != row.event_id:
            raise ShadowReportError(
                f"successful {row.provider} response_id {response_id!r} is reused "
                f"by events {previous_event} and {row.event_id}"
            )
        successful_response_ids[key] = row.event_id

    fingerprints = {
        str(intent.details["qualification_fingerprint"]) for intent in intents.values()
    }
    events: list[ShadowEvent] = []
    seen_stage_ordinals: dict[int, str] = {}
    for event_id, intent in intents.items():
        if intent.shadow_stage_id != expected_stage_id:
            raise ShadowReportError(
                f"event {event_id} belongs to shadow stage "
                f"{intent.shadow_stage_id!r}, expected {expected_stage_id!r}"
            )
        previous_event = seen_stage_ordinals.get(intent.shadow_stage_ordinal)
        if previous_event is not None:
            raise ShadowReportError(
                f"shadow stage ordinal {intent.shadow_stage_ordinal} is reused by "
                f"events {previous_event} and {event_id}"
            )
        seen_stage_ordinals[intent.shadow_stage_ordinal] = event_id
        if intent.details.get("route") != "shadow":
            raise ShadowReportError(f"event {event_id} intent route must be shadow")
        if intent.details.get("authoritative_provider") != "anthropic":
            raise ShadowReportError(
                f"event {event_id} intent authoritative provider must be anthropic"
            )
        if set(intent.expected_calls) != set(PROVIDERS) or any(
            call["requirement"] != "required"
            for call in intent.expected_calls.values()
        ):
            raise ShadowReportError(
                f"event {event_id} shadow intent must require exactly one call per provider"
            )
        if intent.details.get("reserved_calls") != len(PROVIDERS):
            raise ShadowReportError(
                f"event {event_id} shadow intent must reserve two calls"
            )

        event_rows = grouped.get(event_id, [])
        shared = _shared_event_details(event_rows) if event_rows else None
        if shared is not None:
            if shared["route"] != "shadow":
                raise ShadowReportError(f"event {event_id} route must be shadow")
            if shared["authoritative_provider"] != "anthropic":
                raise ShadowReportError(
                    f"event {event_id} authoritative provider must be anthropic"
                )
        providers: dict[str, AuditRow] = {}
        for row in event_rows:
            provider = row.provider
            if provider in providers:
                raise ShadowReportError(
                    f"event {event_id} repeats provider row {provider}"
                )
            if provider not in intent.expected_calls:
                raise ShadowReportError(
                    f"event {event_id} has unexpected terminal provider {provider}"
                )
            if row.created_at < intent.created_at:
                raise ShadowReportError(
                    f"event {event_id} terminal row predates its intent"
                )
            providers[provider] = row

        audit_gap = ""
        missing = sorted(set(PROVIDERS) - providers.keys())
        if intent.details.get("status") in {"pending", "incomplete"}:
            audit_gap = f"intent remains {intent.details['status']}"
        elif missing:
            audit_gap = "missing terminal provider row(s): " + ", ".join(missing)
        else:
            if intent.details.get("terminal_call_count") != len(event_rows):
                raise ShadowReportError(
                    f"event {event_id} terminal_call_count disagrees with rows"
                )
            finalized_at = _parse_created_at(
                intent.details.get("finalized_at"), row_number=0
            )
            if finalized_at < max(row.created_at for row in event_rows):
                raise ShadowReportError(
                    f"event {event_id} finalized before its terminal rows"
                )
            assert shared is not None
            if shared["scoring_contract_version"] != 2:
                raise ShadowReportError(f"event {event_id} scoring contract must be V2")
            for field in INTENT_COMMON_KEYS:
                if (
                    type(shared[field]) is not type(intent.details[field])
                    or shared[field] != intent.details[field]
                ):
                    raise ShadowReportError(
                        f"event {event_id} intent and terminal {field} conflict"
                    )
            for field in ("ai_consent_verified", "openai_allowlist_verified"):
                terminal_value = _common_optional_flag(event_rows, field)
                if (
                    terminal_value is not None
                    and terminal_value != intent.details[field]
                ):
                    raise ShadowReportError(
                        f"event {event_id} intent and terminal {field} conflict"
                    )

        events.append(
            ShadowEvent(
                event_id=event_id,
                event_started_at=intent.event_started_at,
                rows=providers,
                shadow=_common_shadow(event_rows) if not audit_gap else None,
                consent_current=intent.details["ai_consent_verified"],  # type: ignore[arg-type]
                allowlist_eligible=intent.details["openai_allowlist_verified"],  # type: ignore[arg-type]
                qualification_expires_at=str(
                    intent.details["qualification_expires_at"]
                ),
                shadow_stage_ordinal=intent.shadow_stage_ordinal,
                audit_gap=audit_gap,
            )
        )

    if len(fingerprints) != 1:
        raise ShadowReportError(
            "mixed qualification fingerprints in export: "
            + ", ".join(sorted(fingerprints))
        )
    observed_fingerprint = next(iter(fingerprints))
    if observed_fingerprint != expected_fingerprint:
        raise ShadowReportError(
            "qualification fingerprint mismatch: "
            f"expected {expected_fingerprint}, observed {observed_fingerprint}"
        )
    ordered = sorted(events, key=lambda event: event.shadow_stage_ordinal)
    for previous, current in zip(ordered, ordered[1:], strict=False):
        if current.event_started_at < previous.event_started_at:
            raise ShadowReportError(
                f"shadow stage timestamps move backwards between ordinals "
                f"{previous.shadow_stage_ordinal} and {current.shadow_stage_ordinal}"
            )
    return ordered


def load_export(path: Path) -> list[Any]:
    """Read a JSON export only; this helper performs no DB or network access."""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ShadowReportError(f"cannot read export {path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise ShadowReportError(f"export is not valid JSON: {exc}") from exc
    if isinstance(payload, dict):
        if "rows" in payload and "llm_usage" in payload:
            raise ShadowReportError(
                "export must contain either rows or llm_usage, not both"
            )
        payload = payload.get("rows", payload.get("llm_usage"))
    if not isinstance(payload, list):
        raise ShadowReportError(
            "export must be a JSON list or an object with a rows/llm_usage list"
        )
    return payload


def _latency_summary(values: list[int]) -> dict[str, float | int]:
    ordered = sorted(values)
    percentile_95 = ordered[max(0, math.ceil(len(ordered) * 0.95) - 1)]
    return {
        "min_ms": ordered[0],
        "max_ms": ordered[-1],
        "mean_ms": sum(ordered) / len(ordered),
        "p50_ms": (
            ordered[len(ordered) // 2]
            if len(ordered) % 2
            else (ordered[len(ordered) // 2 - 1] + ordered[len(ordered) // 2]) / 2
        ),
        "p95_ms": percentile_95,
    }


def _provider_summary(
    events: list[ShadowEvent], provider: str, rates: ProviderRates
) -> tuple[dict[str, Any], Decimal, Decimal | None]:
    calls = [event.rows[provider].call for event in events]
    outcomes = Counter(str(call["outcome"]) for call in calls)
    error_types = Counter(
        str(call.get("error_type", ""))
        for call in calls
        if call["outcome"] != "success"
    )
    input_tokens = sum(int(call.get("input_tokens", 0)) for call in calls)
    output_tokens = sum(int(call.get("output_tokens", 0)) for call in calls)
    cached_input_tokens = sum(
        int(call.get("cached_input_tokens", 0)) for call in calls
    )
    cache_write_tokens = sum(
        int(call.get("cache_write_tokens", 0)) for call in calls
    )
    if provider == "openai":
        if cached_input_tokens + cache_write_tokens > input_tokens:
            raise ShadowReportError(
                "OpenAI cached and cache-write tokens cannot exceed total input tokens"
            )
        total_cost = (
            Decimal(input_tokens - cached_input_tokens - cache_write_tokens)
            * rates.input_per_million
            + Decimal(cached_input_tokens) * rates.cached_input_per_million
            + Decimal(cache_write_tokens) * rates.cache_write_per_million
            + Decimal(output_tokens) * rates.output_per_million
        ) / Decimal(1_000_000)
    else:
        total_cost = (
            Decimal(input_tokens) * rates.input_per_million
            + Decimal(cached_input_tokens) * rates.cached_input_per_million
            + Decimal(cache_write_tokens) * rates.cache_write_per_million
            + Decimal(output_tokens) * rates.output_per_million
        ) / Decimal(1_000_000)
    successes = outcomes.get("success", 0)
    cost_per_success = total_cost / successes if successes else None
    return (
        {
            "calls": len(calls),
            "successes": successes,
            "failures": len(calls) - successes,
            "outcomes": dict(sorted(outcomes.items())),
            "failure_types": dict(sorted(error_types.items())),
            "tokens": {
                "input": input_tokens,
                "output": output_tokens,
                "cached_input": cached_input_tokens,
                "cache_write": cache_write_tokens,
            },
            "latency": _latency_summary(
                [int(call.get("latency_ms", 0)) for call in calls]
            ),
            "rates_usd_per_million": {
                "input": str(rates.input_per_million),
                "output": str(rates.output_per_million),
                "cached_input": str(rates.cached_input_per_million),
                "cache_write": str(rates.cache_write_per_million),
            },
            "total_cost_usd": float(total_cost),
            "cost_per_success_usd": (
                float(cost_per_success) if cost_per_success is not None else None
            ),
        },
        total_cost,
        cost_per_success,
    )


def _eligibility_summary(
    events: list[ShadowEvent], attribute: str
) -> dict[str, Any]:
    values = [getattr(event, attribute) for event in events]
    present = sum(value is not None for value in values)
    true = sum(value is True for value in values)
    false = sum(value is False for value in values)
    missing = len(values) - present
    status = (
        "verified_from_export"
        if true == len(values)
        else "failed"
        if false
        else "external_verification_required"
    )
    return {
        "status": status,
        "true_events": true,
        "false_events": false,
        "missing_events": missing,
    }


def build_report(
    raw_rows: list[Any],
    *,
    expected_fingerprint: str,
    expected_qualification_expires_at: str,
    expected_stage_id: str,
    event_count: int,
    anthropic_rates: ProviderRates,
    openai_rates: ProviderRates,
    expected_models: dict[str, ProviderIdentity],
    expected_user_id: uuid.UUID,
) -> dict[str, Any]:
    """Validate, select, summarize, and gate the first explicit shadow events."""
    if event_count < 1:
        raise ShadowReportError("event_count must be greater than zero")
    try:
        normalized_stage_id = str(uuid.UUID(expected_stage_id))
    except (AttributeError, ValueError) as exc:
        raise ShadowReportError("expected_stage_id must be a UUID") from exc
    if normalized_stage_id != expected_stage_id:
        raise ShadowReportError("expected_stage_id must use canonical UUID text")
    if (
        len(expected_fingerprint) != 64
        or any(character not in "0123456789abcdef" for character in expected_fingerprint)
    ):
        raise ShadowReportError(
            "expected_fingerprint must be an exact lowercase SHA-256 digest"
        )
    if expected_qualification_expires_at != expected_qualification_expires_at.strip():
        raise ShadowReportError(
            "expected_qualification_expires_at must not contain surrounding whitespace"
        )
    try:
        qualification_expiry = parse_strict_utc_timestamp(
            expected_qualification_expires_at,
            field="expected_qualification_expires_at",
        )
    except ValueError as exc:
        raise ShadowReportError(str(exc)) from exc
    for label, rates in (
        ("anthropic", anthropic_rates),
        ("openai", openai_rates),
    ):
        required_rates = (
            rates.input_per_million,
            rates.output_per_million,
            rates.cached_input_per_million,
            rates.cache_write_per_million,
        )
        if any(not rate.is_finite() or rate <= 0 for rate in required_rates):
            raise ShadowReportError(
                f"{label} input/output/cached-input/cache-write rates must be positive"
            )
    if set(expected_models) != set(PROVIDERS):
        raise ShadowReportError("expected_models must identify Anthropic and OpenAI")
    for provider, identity in expected_models.items():
        if (
            not isinstance(identity, ProviderIdentity)
            or not identity.requested_model.strip()
            or not identity.response_model.strip()
        ):
            raise ShadowReportError(
                f"{provider} requested and response models must be non-empty"
            )
    if not isinstance(expected_user_id, uuid.UUID):
        raise ShadowReportError("expected_user_id must be a UUID")
    terminal_rows, intents = _partition_intents(
        raw_rows,
        expected_models=expected_models,
        expected_user_id=expected_user_id,
    )
    rows = _parse_rows(
        terminal_rows,
        expected_models=expected_models,
        expected_user_id=expected_user_id,
    )
    events = _group_events(
        rows,
        intents,
        expected_fingerprint=expected_fingerprint,
        expected_stage_id=expected_stage_id,
    )
    if len(events) < event_count:
        raise ShadowReportError(
            f"export contains only {len(events)} shadow event intents; "
            f"{event_count} required"
        )
    by_ordinal = {event.shadow_stage_ordinal: event for event in events}
    missing_ordinals = [
        ordinal for ordinal in range(1, event_count + 1) if ordinal not in by_ordinal
    ]
    if missing_ordinals:
        raise ShadowReportError(
            f"shadow stage {expected_stage_id} export is not inclusive from ordinal 1; "
            f"missing required ordinal(s): {', '.join(map(str, missing_ordinals))}"
        )
    selected = [by_ordinal[ordinal] for ordinal in range(1, event_count + 1)]
    observed_expiries = {
        event.qualification_expires_at for event in selected
    }
    if observed_expiries != {expected_qualification_expires_at}:
        raise ShadowReportError(
            "qualification expiry mismatch: expected "
            f"{expected_qualification_expires_at!r}, observed "
            + ", ".join(repr(value) for value in sorted(observed_expiries))
        )
    expired_events = [
        event
        for event in selected
        if event.event_started_at >= qualification_expiry
    ]
    if expired_events:
        first = expired_events[0]
        raise ShadowReportError(
            f"selected event {first.event_id} started at or after the deployed "
            f"qualification expiry {expected_qualification_expires_at}"
        )
    gaps = [event for event in selected if event.audit_gap]
    if gaps:
        first = gaps[0]
        raise ShadowReportError(
            f"selected event {first.event_id} has an unresolved crash gap: "
            f"{first.audit_gap}"
        )

    anthropic, _anthropic_cost, anthropic_cost_per_success = _provider_summary(
        selected, "anthropic", anthropic_rates
    )
    openai, _openai_cost, openai_cost_per_success = _provider_summary(
        selected, "openai", openai_rates
    )
    cost_reduction: Decimal | None = None
    if (
        anthropic_cost_per_success is not None
        and anthropic_cost_per_success > 0
        and openai_cost_per_success is not None
    ):
        cost_reduction = Decimal(1) - (
            openai_cost_per_success / anthropic_cost_per_success
        )

    comparisons = [event.shadow for event in selected if event.shadow is not None]
    missing_comparisons = event_count - len(comparisons)
    behavioral_flips = sum(
        comparison["behavioral_match"] is False for comparison in comparisons
    )
    recall_differences = [
        abs(comparison["authoritative_recall"] - comparison["candidate_recall"])
        for comparison in comparisons
        if comparison.get("authoritative_recall") is not None
        and comparison.get("candidate_recall") is not None
    ]
    eligibility = {
        "current_consent": _eligibility_summary(selected, "consent_current"),
        "allowlist": _eligibility_summary(selected, "allowlist_eligible"),
    }
    shadow_end_to_end_latency = [
        max(
            int(event.rows["anthropic"].call.get("latency_ms", 0)),
            int(event.rows["openai"].call.get("latency_ms", 0)),
        )
        for event in selected
    ]
    candidate_incremental_wait = [
        max(
            0,
            int(event.rows["openai"].call.get("latency_ms", 0))
            - int(event.rows["anthropic"].call.get("latency_ms", 0)),
        )
        for event in selected
    ]

    gate_failures: list[str] = []
    if event_count != DEFAULT_EVENT_COUNT:
        gate_failures.append(
            f"diagnostic sample requested {event_count} event(s); qualification "
            f"requires exactly the first {DEFAULT_EVENT_COUNT} stage ordinals"
        )
    if openai["failures"]:
        gate_failures.append(
            f"OpenAI had {openai['failures']} non-success call(s) in the first "
            f"{event_count} events"
        )
    if anthropic["failures"]:
        gate_failures.append(
            f"Anthropic had {anthropic['failures']} non-success authoritative call(s)"
        )
    if behavioral_flips:
        gate_failures.append(f"shadow had {behavioral_flips} behavioral flip(s)")
    if missing_comparisons:
        gate_failures.append(
            f"shadow comparison was missing for {missing_comparisons} event(s)"
        )
    for label, summary in eligibility.items():
        if summary["status"] == "failed":
            gate_failures.append(f"{label} eligibility was explicitly false")
        elif summary["status"] == "external_verification_required":
            gate_failures.append(f"{label} must be verified externally")
    if cost_reduction is None:
        gate_failures.append("cost reduction is undefined from observed successful calls")
    elif cost_reduction < MIN_COST_REDUCTION:
        gate_failures.append(
            f"cost reduction {cost_reduction:.6f} is below required "
            f"{MIN_COST_REDUCTION:.2f}"
        )

    return {
        "format_version": 1,
        "passed": not gate_failures,
        "selection": {
            "method": "predeclared_shadow_stage_contiguous_ordinal",
            "replacement_policy": "none",
            "shadow_stage_id": expected_stage_id,
            "first_ordinal": 1,
            "last_ordinal": event_count,
            "requested_events": event_count,
            "source_events": len(events),
            "ignored_later_events": len(events) - event_count,
            "event_ids": [event.event_id for event in selected],
            "first_event_started_at": selected[0].event_started_at.isoformat(),
            "last_event_started_at": selected[-1].event_started_at.isoformat(),
            "qualification_sample_complete": event_count == DEFAULT_EVENT_COUNT,
        },
        "qualification_fingerprint": expected_fingerprint,
        "qualification_expires_at": expected_qualification_expires_at,
        "owner_user_id": str(expected_user_id),
        "eligibility": eligibility,
        "providers": {
            "anthropic": {
                **anthropic,
                "requested_model": expected_models["anthropic"].requested_model,
                "response_model": expected_models["anthropic"].response_model,
            },
            "openai": {
                **openai,
                "requested_model": expected_models["openai"].requested_model,
                "response_model": expected_models["openai"].response_model,
            },
        },
        "shadow": {
            "comparisons": len(comparisons),
            "missing_comparisons": missing_comparisons,
            "behavioral_flips": behavioral_flips,
            "within_one_failures": sum(
                comparison["within_one"] is False for comparison in comparisons
            ),
            "recall_difference": {
                "compared": len(recall_differences),
                "mean_absolute": (
                    sum(recall_differences) / len(recall_differences)
                    if recall_differences
                    else None
                ),
                "max_absolute": max(recall_differences) if recall_differences else None,
            },
            "latency": {
                "concurrent_end_to_end": _latency_summary(
                    shadow_end_to_end_latency
                ),
                "candidate_incremental_wait": _latency_summary(
                    candidate_incremental_wait
                ),
            },
        },
        "cost": {
            "method": "observed input/output tokens times explicit CLI rates",
            "openai_vs_anthropic_reduction": (
                float(cost_reduction) if cost_reduction is not None else None
            ),
            "minimum_required_reduction": float(MIN_COST_REDUCTION),
            "gate_passed": (
                cost_reduction is not None
                and cost_reduction >= MIN_COST_REDUCTION
            ),
        },
        "gate_failures": gate_failures,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("export", type=Path, help="JSON llm_usage export")
    parser.add_argument(
        "--expected-fingerprint",
        required=True,
        type=fingerprint_arg,
        help="exact qualified V2 deployment fingerprint",
    )
    parser.add_argument(
        "--expected-shadow-stage-id",
        required=True,
        type=uuid.UUID,
        help="predeclared production shadow stage UUID; export must include ordinal 1",
    )
    parser.add_argument(
        "--expected-qualification-expires-at",
        required=True,
        type=qualification_expiry_arg,
        help="exact deployed strict-UTC qualification expiry bound to every event",
    )
    parser.add_argument(
        "--event-count",
        type=positive_int,
        default=DEFAULT_EVENT_COUNT,
        help=f"first event count; defaults to {DEFAULT_EVENT_COUNT}",
    )
    parser.add_argument(
        "--expected-owner-user-id",
        required=True,
        type=uuid.UUID,
        help="single owner UUID authorized for this canary",
    )
    for provider in PROVIDERS:
        parser.add_argument(
            f"--expected-{provider}-model",
            required=True,
            help="exact requested model identifier present in every audit row",
        )
        parser.add_argument(
            f"--expected-{provider}-response-model",
            required=True,
            help="exact returned model identifier present in every successful audit row",
        )
        parser.add_argument(
            f"--{provider}-input-usd-per-million",
            required=True,
            type=positive_decimal,
        )
        parser.add_argument(
            f"--{provider}-output-usd-per-million",
            required=True,
            type=positive_decimal,
        )
        parser.add_argument(
            f"--{provider}-cached-input-usd-per-million",
            required=True,
            type=positive_decimal,
        )
    parser.add_argument(
        "--anthropic-cache-write-usd-per-million",
        required=True,
        type=positive_decimal,
    )
    parser.add_argument(
        "--openai-cache-write-usd-per-million",
        required=True,
        type=positive_decimal,
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="emit only the machine-readable JSON report",
    )
    return parser


def _print_human(report: dict[str, Any]) -> None:
    status = "PASS" if report["passed"] else "FAIL"
    selection = report["selection"]
    print(f"OpenAI V2 shadow report: {status}")
    print(
        f"  events             {selection['requested_events']} of "
        f"{selection['source_events']} (first only; no replacement)"
    )
    for provider in PROVIDERS:
        summary = report["providers"][provider]
        print(
            f"  {provider:<18} calls={summary['calls']} success={summary['successes']} "
            f"failure={summary['failures']} in={summary['tokens']['input']} "
            f"out={summary['tokens']['output']} "
            f"cost/success={summary['cost_per_success_usd']}"
        )
    print(
        "  reduction          "
        f"{report['cost']['openai_vs_anthropic_reduction']} "
        f"(required >= {report['cost']['minimum_required_reduction']})"
    )
    print(
        f"  behavioral flips   {report['shadow']['behavioral_flips']}"
    )
    for label, summary in report["eligibility"].items():
        print(f"  {label:<18} {summary['status']}")
    if report["gate_failures"]:
        print("  gate failures:")
        for failure in report["gate_failures"]:
            print(f"    - {failure}")


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    try:
        rows = load_export(args.export)
        report = build_report(
            rows,
            expected_fingerprint=args.expected_fingerprint,
            expected_qualification_expires_at=(
                args.expected_qualification_expires_at
            ),
            expected_stage_id=str(args.expected_shadow_stage_id),
            event_count=args.event_count,
            anthropic_rates=ProviderRates(
                args.anthropic_input_usd_per_million,
                args.anthropic_output_usd_per_million,
                args.anthropic_cached_input_usd_per_million,
                args.anthropic_cache_write_usd_per_million,
            ),
            openai_rates=ProviderRates(
                args.openai_input_usd_per_million,
                args.openai_output_usd_per_million,
                args.openai_cached_input_usd_per_million,
                args.openai_cache_write_usd_per_million,
            ),
            expected_models={
                "anthropic": ProviderIdentity(
                    args.expected_anthropic_model,
                    args.expected_anthropic_response_model,
                ),
                "openai": ProviderIdentity(
                    args.expected_openai_model,
                    args.expected_openai_response_model,
                ),
            },
            expected_user_id=args.expected_owner_user_id,
        )
    except ShadowReportError as exc:
        print(f"shadow report rejected: {exc}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        _print_human(report)
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
