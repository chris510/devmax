import json
import uuid
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from app.services.scoring_provider import compare_shadow_results
from scripts import openai_shadow_report as report

FINGERPRINT = "f" * 64
ANTHROPIC_RATES = report.ProviderRates(
    Decimal("10"), Decimal("10"), Decimal("1"), Decimal("12.5")
)
OPENAI_RATES = report.ProviderRates(
    Decimal("1"), Decimal("1"), Decimal("1"), Decimal("1")
)
EXPECTED_MODELS = {
    "anthropic": report.ProviderIdentity("claude-test", "claude-test"),
    "openai": report.ProviderIdentity("luna-test", "luna-test"),
}
BASE_TIME = datetime(2026, 8, 13, 12, tzinfo=UTC)
OWNER_USER_ID = uuid.UUID("11111111-1111-4111-8111-111111111111")
SHADOW_STAGE_ID = "22222222-2222-4222-8222-222222222222"
QUALIFICATION_EXPIRY = "2026-09-01T00:00:00Z"


def event_rows(
    index: int,
    *,
    fingerprint: str = FINGERPRINT,
    openai_outcome: str = "success",
    behavioral_match: bool = True,
    consent_current: bool | None = True,
    allowlist_eligible: bool | None = True,
    openai_cached_input: int = 5,
    openai_cache_write: int = 0,
    event_started_at: datetime | None = None,
    row_created_at: datetime | None = None,
) -> list[dict[str, Any]]:
    event_id = f"event-{index:03d}"
    started_at = event_started_at or (BASE_TIME + timedelta(seconds=index))
    created_at = row_created_at or started_at
    include_shadow = openai_outcome == "success"
    shadow = {
        "authoritative_recall": 4,
        "candidate_recall": 4 if behavioral_match else 2,
        "within_one": behavioral_match,
        "behavioral_match": behavioral_match,
        "authoritative_decisions": {
            "flow": "complete",
            "scheduler": "good",
            "today_band": "solid",
            "coverage_tier": "solid",
        },
        "candidate_decisions": (
            {
                "flow": "complete",
                "scheduler": "good",
                "today_band": "solid",
                "coverage_tier": "solid",
            }
            if behavioral_match
            else {
                "flow": "complete",
                "scheduler": "again",
                "today_band": "shaky",
                "coverage_tier": "shaky",
            }
        ),
    }
    rows: list[dict[str, Any]] = []
    for provider in ("anthropic", "openai"):
        outcome = openai_outcome if provider == "openai" else "success"
        details: dict[str, Any] = {
            "route": "shadow",
            "authoritative_provider": "anthropic",
            "qualification_fingerprint": fingerprint,
            "qualification_expires_at": QUALIFICATION_EXPIRY,
            "fallback_reason": "",
            "candidate_error": (
                "OpenAIResponsesError"
                if openai_outcome != "success"
                else ""
            ),
            "scoring_event_id": event_id,
            "event_started_at": started_at.isoformat(),
            "session_id": f"session-{index:03d}",
            "scoring_contract_version": 2,
            "probes_used": index % 3,
            "ai_consent_policy_version": report.AI_CONSENT_POLICY_VERSION,
            "call": {
                "provider": provider,
                "model": "claude-test" if provider == "anthropic" else "luna-test",
                "response_model": (
                    "claude-test" if provider == "anthropic" else "luna-test"
                ),
                "response_id": f"response-{index:03d}-{provider}",
                "latency_ms": 20 if provider == "anthropic" else 10,
                "input_tokens": 100,
                "output_tokens": 10,
                "cached_input_tokens": (
                    0 if provider == "anthropic" else openai_cached_input
                ),
                "cache_write_tokens": (
                    0 if provider == "anthropic" else openai_cache_write
                ),
                "outcome": outcome,
                "error_type": (
                    "OpenAIResponsesError"
                    if provider == "openai" and outcome != "success"
                    else ""
                ),
            },
        }
        if include_shadow:
            details["shadow"] = shadow
        if consent_current is not None:
            details["ai_consent_verified"] = consent_current
        if allowlist_eligible is not None:
            details["openai_allowlist_verified"] = allowlist_eligible
        rows.append(
            {
                "id": f"row-{index:03d}-{provider}",
                "user_id": str(OWNER_USER_ID),
                "operation": "score_v2",
                "created_at": created_at.isoformat(),
                "details": details,
            }
        )
    rows.append(
        {
            "id": f"row-{index:03d}-intent",
            "user_id": str(OWNER_USER_ID),
            "operation": "score_v2_intent",
            "created_at": started_at.isoformat(),
            "details": {
                "audit_type": "scoring_event_intent",
                "manifest_version": 1,
                "status": "finalized",
                "reserved_calls": 2,
                "finalized_at": created_at.isoformat(),
                "terminal_call_count": 2,
                "shadow_stage_id": SHADOW_STAGE_ID,
                "shadow_stage_ordinal": index + 1,
                "route": "shadow",
                "authoritative_provider": "anthropic",
                "qualification_fingerprint": fingerprint,
                "qualification_expires_at": QUALIFICATION_EXPIRY,
                "scoring_event_id": event_id,
                "event_started_at": started_at.isoformat(),
                "session_id": f"session-{index:03d}",
                "scoring_contract_version": 2,
                "probes_used": index % 3,
                "ai_consent_policy_version": report.AI_CONSENT_POLICY_VERSION,
                "ai_consent_verified": consent_current,
                "openai_allowlist_verified": allowlist_eligible,
                "expected_calls": [
                    {
                        "provider": "anthropic",
                        "model": "claude-test",
                        "requirement": "required",
                    },
                    {
                        "provider": "openai",
                        "model": "luna-test",
                        "requirement": "required",
                    },
                ],
            },
        }
    )
    return rows


def build(
    rows: list[dict[str, Any]],
    *,
    event_count: int,
    anthropic_rates: report.ProviderRates = ANTHROPIC_RATES,
    openai_rates: report.ProviderRates = OPENAI_RATES,
) -> dict[str, Any]:
    return report.build_report(
        rows,
        expected_fingerprint=FINGERPRINT,
        expected_qualification_expires_at=QUALIFICATION_EXPIRY,
        expected_stage_id=SHADOW_STAGE_ID,
        event_count=event_count,
        anthropic_rates=anthropic_rates,
        openai_rates=openai_rates,
        expected_models=EXPECTED_MODELS,
        expected_user_id=OWNER_USER_ID,
    )


def test_small_clean_export_is_valid_but_explicitly_non_qualifying() -> None:
    rows = [*event_rows(2), *event_rows(0), *event_rows(1)]
    rows.reverse()  # Export row order is deliberately irrelevant.

    result = build(rows, event_count=3)

    assert result["passed"] is False
    assert result["selection"] == {
        "method": "predeclared_shadow_stage_contiguous_ordinal",
        "replacement_policy": "none",
        "shadow_stage_id": SHADOW_STAGE_ID,
        "first_ordinal": 1,
        "last_ordinal": 3,
        "requested_events": 3,
        "source_events": 3,
        "ignored_later_events": 0,
        "event_ids": ["event-000", "event-001", "event-002"],
        "first_event_started_at": BASE_TIME.isoformat(),
        "last_event_started_at": (BASE_TIME + timedelta(seconds=2)).isoformat(),
        "qualification_sample_complete": False,
    }
    assert result["providers"]["anthropic"]["calls"] == 3
    assert result["providers"]["openai"]["calls"] == 3
    assert result["providers"]["openai"]["tokens"] == {
        "input": 300,
        "output": 30,
        "cached_input": 15,
        "cache_write": 0,
    }
    assert result["providers"]["openai"]["latency"]["mean_ms"] == 10
    assert result["shadow"]["latency"]["concurrent_end_to_end"]["p95_ms"] == 20
    assert result["shadow"]["latency"]["candidate_incremental_wait"]["p95_ms"] == 0
    assert result["cost"]["openai_vs_anthropic_reduction"] == pytest.approx(0.9)
    assert result["eligibility"]["current_consent"]["status"] == (
        "verified_from_export"
    )
    assert result["gate_failures"] == [
        "diagnostic sample requested 3 event(s); qualification requires exactly "
        "the first 100 stage ordinals"
    ]


@pytest.mark.parametrize(("field", "value"), [("input_tokens", 0), ("output_tokens", 0)])
def test_successful_shadow_call_requires_positive_usage(
    field: str, value: int
) -> None:
    rows = event_rows(0)
    rows[1]["details"]["call"][field] = value

    with pytest.raises(
        report.ShadowReportError,
        match="successful call requires positive input and output tokens",
    ):
        build(rows, event_count=1)


def test_successful_shadow_call_cannot_carry_an_error_type() -> None:
    rows = event_rows(0)
    rows[1]["details"]["call"]["error_type"] = "unexpected"

    with pytest.raises(
        report.ShadowReportError,
        match="successful call cannot have error_type",
    ):
        build(rows, event_count=1)


def test_first_100_never_replaces_a_failure_with_event_101() -> None:
    rows: list[dict[str, Any]] = []
    for index in range(101):
        rows.extend(
            event_rows(
                index,
                openai_outcome="technical_error" if index == 99 else "success",
            )
        )
    rows.reverse()

    result = build(rows, event_count=100)

    assert result["selection"]["event_ids"][-1] == "event-099"
    assert "event-100" not in result["selection"]["event_ids"]
    assert result["selection"]["ignored_later_events"] == 1
    assert result["providers"]["openai"]["calls"] == 100
    assert result["providers"]["openai"]["failures"] == 1
    assert result["providers"]["openai"]["failure_types"] == {
        "OpenAIResponsesError": 1
    }
    assert result["passed"] is False


def test_first_100_never_replaces_event_99_crash_gap_with_event_100() -> None:
    rows = [row for index in range(101) for row in event_rows(index)]
    rows = [
        row
        for row in rows
        if not (
            row["operation"] == "score_v2"
            and row["details"]["scoring_event_id"] == "event-099"
        )
    ]
    intent = next(
        row
        for row in rows
        if row["operation"] == "score_v2_intent"
        and row["details"]["scoring_event_id"] == "event-099"
    )
    intent["details"].update(
        status="pending",
        finalized_at=None,
        terminal_call_count=0,
    )

    with pytest.raises(
        report.ShadowReportError,
        match="selected event event-099 has an unresolved crash gap",
    ):
        build(rows, event_count=100)


def test_export_of_ordinals_two_through_101_cannot_replace_missing_first_event() -> None:
    rows = [row for index in range(1, 101) for row in event_rows(index)]

    with pytest.raises(
        report.ShadowReportError,
        match="export is not inclusive from ordinal 1; missing required ordinal.*1",
    ):
        build(rows, event_count=100)


def test_partial_shadow_terminal_set_is_an_unresolved_audit_gap() -> None:
    rows = event_rows(0)
    rows = [
        row
        for row in rows
        if not (
            row["operation"] == "score_v2"
            and row["details"]["call"]["provider"] == "openai"
        )
    ]
    intent = next(row for row in rows if row["operation"] == "score_v2_intent")
    intent["details"].update(
        status="incomplete",
        finalized_at=None,
        terminal_call_count=1,
    )

    with pytest.raises(
        report.ShadowReportError,
        match="selected event event-000 has an unresolved crash gap: intent remains incomplete",
    ):
        build(rows, event_count=1)


def test_ninety_nine_clean_events_are_diagnostic_not_qualifying() -> None:
    rows = [row for index in range(99) for row in event_rows(index)]
    result = build(rows, event_count=99)

    assert result["passed"] is False
    assert result["selection"]["qualification_sample_complete"] is False
    assert "qualification requires exactly the first 100" in result["gate_failures"][0]


def test_clean_hundred_event_sample_can_qualify() -> None:
    rows = [row for index in range(100) for row in event_rows(index)]
    result = build(rows, event_count=100)

    assert result["passed"] is True
    assert result["selection"]["qualification_sample_complete"] is True


def test_later_cheap_events_cannot_dilute_the_immutable_first_hundred_gate() -> None:
    rows = [row for index in range(200) for row in event_rows(index)]
    for row in rows:
        if row["operation"] != "score_v2":
            continue
        ordinal = int(row["details"]["scoring_event_id"].split("-")[1]) + 1
        provider = row["details"]["call"]["provider"]
        if ordinal <= 100 and provider == "openai":
            row["details"]["call"]["input_tokens"] = 1000
            row["details"]["call"]["output_tokens"] = 1000
        elif ordinal > 100 and provider == "openai":
            row["details"]["call"]["input_tokens"] = 1
            row["details"]["call"]["output_tokens"] = 1
            row["details"]["call"]["cached_input_tokens"] = 0
        elif ordinal > 100:
            row["details"]["call"]["input_tokens"] = 1000
            row["details"]["call"]["output_tokens"] = 1000

    result = build(rows, event_count=200)

    assert result["cost"]["gate_passed"] is True
    assert result["passed"] is False
    assert result["selection"]["qualification_sample_complete"] is False
    assert "requires exactly the first 100" in result["gate_failures"][0]


def test_selection_uses_call_start_not_faster_usage_row_completion() -> None:
    earlier_slow = event_rows(
        0,
        event_started_at=BASE_TIME,
        row_created_at=BASE_TIME + timedelta(seconds=100),
    )
    later_fast = event_rows(
        1,
        event_started_at=BASE_TIME + timedelta(seconds=1),
        row_created_at=BASE_TIME + timedelta(seconds=2),
    )

    result = build([*later_fast, *earlier_slow], event_count=1)

    assert result["selection"]["event_ids"] == ["event-000"]
    assert result["selection"]["first_event_started_at"] == BASE_TIME.isoformat()


def test_event_started_at_is_required_and_must_match_both_provider_rows() -> None:
    missing = event_rows(0)
    del missing[0]["details"]["event_started_at"]
    with pytest.raises(report.ShadowReportError, match="missing=.*event_started_at"):
        build(missing, event_count=1)

    conflicting = event_rows(0)
    conflicting[1]["details"]["event_started_at"] = (
        BASE_TIME + timedelta(seconds=1)
    ).isoformat()
    with pytest.raises(
        report.ShadowReportError,
        match="conflicting event_started_at values",
    ):
        build(conflicting, event_count=1)


def test_qualification_expiry_must_exactly_match_the_deployed_value() -> None:
    rows = event_rows(0)
    for row in rows:
        row["details"]["qualification_expires_at"] = "2026-09-02T00:00:00Z"

    with pytest.raises(report.ShadowReportError, match="qualification expiry mismatch"):
        build(rows, event_count=1)


def test_selected_event_must_start_before_qualification_expiry() -> None:
    rows = event_rows(
        0,
        event_started_at=datetime.fromisoformat(
            QUALIFICATION_EXPIRY.replace("Z", "+00:00")
        ),
    )

    with pytest.raises(
        report.ShadowReportError,
        match="started at or after the deployed qualification expiry",
    ):
        build(rows, event_count=1)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("session_id", "another-session"),
        ("probes_used", 2),
        ("fallback_reason", "tampered-fallback"),
        ("candidate_error", "tampered-error"),
        ("route", "primary"),
        ("authoritative_provider", "openai"),
        ("qualification_fingerprint", "e" * 64),
        ("scoring_contract_version", 1),
    ],
)
def test_event_rows_must_have_exact_shared_metadata(
    field: str, value: object
) -> None:
    rows = event_rows(0)
    rows[1]["details"][field] = value

    with pytest.raises(report.ShadowReportError, match=f"conflicting {field} values"):
        build(rows, event_count=1)


def test_event_rows_must_share_the_exact_consent_policy_version() -> None:
    rows = event_rows(0)
    rows[1]["details"]["ai_consent_policy_version"] = "stale-policy"

    with pytest.raises(
        report.ShadowReportError,
        match="conflicting ai_consent_policy_version values",
    ):
        build(rows, event_count=1)


def test_consent_policy_version_is_required_on_every_call_row() -> None:
    rows = event_rows(0)
    del rows[0]["details"]["ai_consent_policy_version"]

    with pytest.raises(
        report.ShadowReportError,
        match="missing=.*ai_consent_policy_version",
    ):
        build(rows, event_count=1)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("session_id", 7, "session_id must be non-empty text"),
        ("session_id", "", "session_id must be non-empty text"),
        ("probes_used", True, "probes_used must be a non-negative integer"),
        ("probes_used", "0", "probes_used must be a non-negative integer"),
        ("probes_used", -1, "probes_used must be a non-negative integer"),
        ("fallback_reason", None, "fallback_reason must be text"),
        ("candidate_error", False, "candidate_error must be text"),
        (
            "ai_consent_policy_version",
            2,
            "ai_consent_policy_version must be non-empty text",
        ),
    ],
)
def test_event_metadata_uses_strict_types(
    field: str, value: object, message: str
) -> None:
    rows = event_rows(0)
    rows[0]["details"][field] = value

    with pytest.raises(report.ShadowReportError, match=message):
        build(rows, event_count=1)


def test_shadow_report_requires_current_consent_policy_version() -> None:
    rows = event_rows(0)
    for row in rows[:2]:
        row["details"]["ai_consent_policy_version"] = "stale-policy"

    with pytest.raises(
        report.ShadowReportError,
        match="must equal the current policy version",
    ):
        build(rows, event_count=1)


def test_behavioral_flip_fails_an_otherwise_clean_shadow() -> None:
    result = build(
        [*event_rows(0), *event_rows(1, behavioral_match=False)],
        event_count=2,
    )

    assert result["providers"]["openai"]["failures"] == 0
    assert result["shadow"]["behavioral_flips"] == 1
    assert result["shadow"]["recall_difference"] == {
        "compared": 2,
        "mean_absolute": 1.0,
        "max_absolute": 2,
    }
    assert result["passed"] is False
    assert "shadow had 1 behavioral flip(s)" in result["gate_failures"]


def test_follow_up_shadow_accepts_runtime_provisional_recall_values() -> None:
    rows = event_rows(0)
    comparison = asdict(
        compare_shadow_results(
            authoritative_status="follow_up",
            authoritative_recall=4,
            candidate_status="follow_up",
            candidate_recall=4,
        )
    )
    for row in rows[:2]:
        row["details"]["shadow"] = comparison

    result = build(rows, event_count=1)

    assert result["shadow"]["behavioral_flips"] == 0
    assert result["shadow"]["within_one_failures"] == 0


@pytest.mark.parametrize(
    ("tamper", "message"),
    [
        ("extra-key", "shape is invalid"),
        ("decision", "does not match its primitive flow/recall"),
        ("within-one", "within_one disagrees with recalls"),
        ("behavioral-match", "behavioral_match disagrees with decisions"),
    ],
)
def test_shadow_comparison_tampering_is_rejected(
    tamper: str, message: str
) -> None:
    rows = event_rows(0)
    for row in rows[:2]:
        shadow = row["details"]["shadow"]
        if tamper == "extra-key":
            shadow["untrusted"] = True
        elif tamper == "decision":
            shadow["candidate_decisions"]["scheduler"] = "again"
        elif tamper == "within-one":
            shadow["within_one"] = False
        else:
            shadow["behavioral_match"] = False

    with pytest.raises(report.ShadowReportError, match=message):
        build(rows, event_count=1)


def test_cost_reduction_gate_uses_the_explicit_rates() -> None:
    result = build(
        event_rows(0),
        event_count=1,
        openai_rates=report.ProviderRates(
            Decimal("2"), Decimal("2"), Decimal("2"), Decimal("2")
        ),
    )

    assert result["cost"]["openai_vs_anthropic_reduction"] == pytest.approx(0.8)
    assert result["cost"]["gate_passed"] is False
    assert result["passed"] is False
    assert "below required 0.85" in result["gate_failures"][-1]


def test_provider_specific_cached_input_is_billed_with_explicit_rates() -> None:
    result = build(
        event_rows(0, openai_cache_write=10),
        event_count=1,
        anthropic_rates=report.ProviderRates(
            Decimal("2"), Decimal("3"), Decimal("0.2"), Decimal("2.5")
        ),
        openai_rates=report.ProviderRates(
            Decimal("2"), Decimal("3"), Decimal("0.2"), Decimal("1.5")
        ),
    )

    assert result["providers"]["anthropic"]["total_cost_usd"] == pytest.approx(
        0.00023
    )
    assert result["providers"]["openai"]["total_cost_usd"] == pytest.approx(
        0.000216
    )


def test_openai_cached_plus_write_cannot_exceed_total_input() -> None:
    with pytest.raises(
        report.ShadowReportError,
        match="cached and cache-write tokens exceed total input",
    ):
        build(
            event_rows(0, openai_cached_input=95, openai_cache_write=6),
            event_count=1,
        )


@pytest.mark.parametrize("problem", ["duplicate-provider", "missing-provider"])
def test_ambiguous_provider_group_is_rejected(problem: str) -> None:
    rows = event_rows(0)
    if problem == "duplicate-provider":
        duplicate = json.loads(json.dumps(rows[0]))
        duplicate["id"] = "another-anthropic-row"
        rows.append(duplicate)
        message = "repeats provider row anthropic"
    else:
        rows.pop(1)
        message = "unresolved crash gap"

    with pytest.raises(report.ShadowReportError, match=message):
        build(rows, event_count=1)


def test_terminal_rows_without_an_intent_are_rejected() -> None:
    rows = [row for row in event_rows(0) if row["operation"] == "score_v2"]

    with pytest.raises(report.ShadowReportError, match="no pre-call intent"):
        build(rows, event_count=1)


def test_duplicate_intent_for_one_event_is_rejected() -> None:
    rows = event_rows(0)
    duplicate = json.loads(json.dumps(rows[-1]))
    duplicate["id"] = "duplicate-intent-row"
    rows.append(duplicate)

    with pytest.raises(report.ShadowReportError, match="duplicate intent rows"):
        build(rows, event_count=1)


def test_replayed_successful_response_id_across_events_is_rejected() -> None:
    rows = [*event_rows(0), *event_rows(1)]
    first = next(
        row
        for row in rows
        if row["operation"] == "score_v2"
        and row["details"]["scoring_event_id"] == "event-000"
        and row["details"]["call"]["provider"] == "openai"
    )
    replay = next(
        row
        for row in rows
        if row["operation"] == "score_v2"
        and row["details"]["scoring_event_id"] == "event-001"
        and row["details"]["call"]["provider"] == "openai"
    )
    replay["details"]["call"]["response_id"] = first["details"]["call"][
        "response_id"
    ]

    with pytest.raises(report.ShadowReportError, match="response_id .* is reused"):
        build(rows, event_count=1)


def test_intent_and_terminal_timestamp_order_is_enforced() -> None:
    intent_before_event = event_rows(0)
    intent_before_event[-1]["created_at"] = (
        BASE_TIME - timedelta(seconds=1)
    ).isoformat()
    with pytest.raises(report.ShadowReportError, match="created before event_started_at"):
        build(intent_before_event, event_count=1)

    terminal_before_intent = event_rows(0)
    terminal_before_intent[-1]["created_at"] = (
        BASE_TIME + timedelta(seconds=1)
    ).isoformat()
    terminal_before_intent[-1]["details"]["finalized_at"] = (
        BASE_TIME + timedelta(seconds=2)
    ).isoformat()
    with pytest.raises(report.ShadowReportError, match="terminal row predates its intent"):
        build(terminal_before_intent, event_count=1)


def test_missing_event_id_is_rejected() -> None:
    rows = event_rows(0)
    del rows[0]["details"]["scoring_event_id"]

    with pytest.raises(report.ShadowReportError, match="missing=.*scoring_event_id"):
        build(rows, event_count=1)


@pytest.mark.parametrize(
    ("key", "value", "top_level"),
    [
        ("transcript", "learner words", False),
        ("question_asked", "What is the answer?", False),
        ("answer_text", "sensitive answer", False),
        ("rubric", "grading text", False),
        ("mastery_summary", "model feedback", False),
        ("raw_transcript_text", "learner words", True),
    ],
)
def test_content_like_telemetry_key_is_rejected(
    key: str, value: str, top_level: bool
) -> None:
    rows = event_rows(0)
    target = rows[0] if top_level else rows[0]["details"].setdefault("diagnostic", {})
    target[key] = value

    with pytest.raises(report.ShadowReportError, match="content-like telemetry key"):
        build(rows, event_count=1)


@pytest.mark.parametrize("location", ["row", "details", "call"])
def test_neutral_unknown_telemetry_key_is_rejected_by_exact_shape(
    location: str,
) -> None:
    rows = event_rows(0)
    target = (
        rows[0]
        if location == "row"
        else rows[0]["details"]
        if location == "details"
        else rows[0]["details"]["call"]
    )
    target["diagnostic_blob"] = "opaque"

    with pytest.raises(report.ShadowReportError, match="shape is invalid"):
        build(rows, event_count=1)


def test_every_shadow_row_must_belong_to_the_single_expected_owner() -> None:
    rows = event_rows(0)
    rows[1]["user_id"] = str(uuid.uuid4())

    with pytest.raises(report.ShadowReportError, match="outside the owner canary"):
        build(rows, event_count=1)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("model", "another-luna", "requested model"),
        ("response_model", "another-snapshot", "response model"),
        ("response_id", "", "response id"),
    ],
)
def test_successful_call_requires_exact_model_and_response_identity(
    field: str, value: str, message: str
) -> None:
    rows = event_rows(0)
    rows[1]["details"]["call"][field] = value

    with pytest.raises(report.ShadowReportError, match=message):
        build(rows, event_count=1)


def test_mixed_fingerprints_are_rejected_before_selection() -> None:
    rows = [*event_rows(0), *event_rows(1, fingerprint="e" * 64)]

    with pytest.raises(report.ShadowReportError, match="mixed qualification"):
        build(rows, event_count=1)


def test_fewer_than_the_explicit_event_count_is_rejected() -> None:
    with pytest.raises(report.ShadowReportError, match="only 2 shadow event intents"):
        build([*event_rows(0), *event_rows(1)], event_count=3)


def test_missing_eligibility_flags_are_clearly_external_checks() -> None:
    result = build(
        event_rows(0, consent_current=None, allowlist_eligible=None),
        event_count=1,
    )

    assert result["eligibility"]["current_consent"]["status"] == (
        "external_verification_required"
    )
    assert result["eligibility"]["allowlist"]["status"] == (
        "external_verification_required"
    )
    assert "current_consent must be verified externally" in result["gate_failures"]
    assert "allowlist must be verified externally" in result["gate_failures"]
    assert result["passed"] is False


def test_json_cli_is_read_only_and_machine_parseable(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    export = tmp_path / "llm-usage.json"
    export.write_text(json.dumps({"rows": event_rows(0)}), encoding="utf-8")

    exit_code = report.main(
        [
            str(export),
            "--expected-fingerprint",
            FINGERPRINT,
            "--expected-shadow-stage-id",
            SHADOW_STAGE_ID,
            "--expected-qualification-expires-at",
            QUALIFICATION_EXPIRY,
            "--event-count",
            "1",
            "--expected-owner-user-id",
            str(OWNER_USER_ID),
            "--expected-anthropic-model",
            "claude-test",
            "--expected-anthropic-response-model",
            "claude-test",
            "--expected-openai-model",
            "luna-test",
            "--expected-openai-response-model",
            "luna-test",
            "--anthropic-input-usd-per-million",
            "10",
            "--anthropic-output-usd-per-million",
            "10",
            "--anthropic-cached-input-usd-per-million",
            "1",
            "--anthropic-cache-write-usd-per-million",
            "12.5",
            "--openai-input-usd-per-million",
            "1",
            "--openai-output-usd-per-million",
            "1",
            "--openai-cached-input-usd-per-million",
            "0.1",
            "--openai-cache-write-usd-per-million",
            "0.5",
            "--json",
        ]
    )

    output = capsys.readouterr()
    parsed = json.loads(output.out)
    assert exit_code == 1
    assert output.err == ""
    assert parsed["passed"] is False
    assert parsed["selection"]["event_ids"] == ["event-000"]
    assert list(tmp_path.iterdir()) == [export]


def test_account_export_llm_usage_shape_is_accepted(tmp_path: Path) -> None:
    export = tmp_path / "account-export.json"
    export.write_text(json.dumps({"llm_usage": event_rows(0)}), encoding="utf-8")

    assert report.load_export(export) == event_rows(0)
