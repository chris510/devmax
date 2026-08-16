"""Pure routing, qualification, and comparison tests for scoring providers."""

from __future__ import annotations

import hashlib
import hmac
import itertools
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from app.config import Settings
from app.consent_policy import LATEST_POLICY_VERSION
from app.services import scoring_provider

GOOD = {
    "database_url": "postgresql+asyncpg://u:p@host/db",
    "api_key": "api-key-0000000000000000000000000000",
    "cron_secret": "cron-secret-00000000000000000000000000",
    "ai_consent_enforcement_enabled": False,
}
USER_A = uuid.UUID("11111111-1111-4111-8111-111111111111")
USER_B = uuid.UUID("22222222-2222-4222-8222-222222222222")
FINGERPRINT = "ab" * 32
SAFETY_SECRET = "safety-secret-which-is-at-least-32-characters"
QUALIFICATION_EXPIRY = (datetime.now(UTC) + timedelta(days=1)).isoformat()
SHADOW_STAGE_ID = "33333333-3333-4333-8333-333333333333"


def settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "scoring_model": "claude-qualified",
        "scoring_effort": "low",
        "anthropic_api_key": "anthropic-test-key",
        "openai_api_key": "sk-test",
        "openai_v2_scoring_model": "gpt-5.6-luna",
        "openai_v2_scoring_effort": "medium",
        "openai_v2_scoring_mode": "primary",
        "openai_v2_scoring_user_ids": str(USER_A),
        "openai_v2_scoring_qualification_fingerprint": FINGERPRINT,
        "openai_v2_scoring_qualification_expires_at": QUALIFICATION_EXPIRY,
        "openai_v2_scoring_shadow_stage_id": SHADOW_STAGE_ID,
        "openai_safety_identifier_secret": SAFETY_SECRET,
        "ai_consent_enforcement_enabled": True,
        "ai_consent_required_policy_version": LATEST_POLICY_VERSION,
        "scoring_contract_version": 2,
    }
    values.update(overrides)
    return Settings(_env_file=None, **{**GOOD, **values})


@pytest.mark.parametrize(
    ("mode", "user_id", "contract_version"),
    [
        ("off", USER_A, 2),
        ("primary", USER_B, 2),
        ("primary", USER_A, 1),
        ("shadow", USER_A, 1),
    ],
)
def test_off_allowlist_and_v1_overrides_force_anthropic(
    mode: str, user_id: uuid.UUID, contract_version: int
) -> None:
    configured = settings(openai_v2_scoring_mode=mode)

    route = scoring_provider.route_for_session(
        configured,
        user_id=user_id,
        scoring_contract_version=contract_version,
    )

    assert route == scoring_provider.anthropic_route(configured)
    assert route.mode == scoring_provider.ROUTE_ANTHROPIC
    assert route.openai_model == ""


@pytest.mark.parametrize(
    ("configured_mode", "expected_mode"),
    [("shadow", scoring_provider.ROUTE_SHADOW), ("primary", scoring_provider.ROUTE_PRIMARY)],
)
def test_allowlisted_v2_session_receives_the_configured_route(
    configured_mode: str, expected_mode: str
) -> None:
    configured = settings(
        openai_v2_scoring_mode=configured_mode,
        openai_v2_scoring_qualification_fingerprint=FINGERPRINT.upper(),
    )

    route = scoring_provider.route_for_session(
        configured,
        user_id=USER_A,
        scoring_contract_version=2,
    )

    assert route.mode == expected_mode
    assert route.anthropic_model == "claude-qualified"
    assert route.anthropic_effort == "low"
    assert route.openai_model == "gpt-5.6-luna"
    assert route.openai_effort == "medium"
    assert route.qualification_fingerprint == FINGERPRINT


def test_elapsed_qualification_forces_new_sessions_to_anthropic() -> None:
    configured = settings()
    expiry = configured.openai_v2_scoring_qualification_expiry

    assert scoring_provider.qualification_is_current(
        configured, now=expiry - timedelta(microseconds=1)
    )
    assert not scoring_provider.qualification_is_current(
        configured, now=expiry
    )
    configured.openai_v2_scoring_qualification_expires_at = (
        "2020-01-01T00:00:00Z"
    )
    assert scoring_provider.route_for_session(
        configured,
        user_id=USER_A,
        scoring_contract_version=2,
    ).mode == scoring_provider.ROUTE_ANTHROPIC


def test_route_eligibility_reports_expiry_for_an_already_frozen_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configured = settings()
    route = scoring_provider.route_for_session(
        configured,
        user_id=USER_A,
        scoring_contract_version=2,
    )
    monkeypatch.setattr(
        configured,
        "openai_v2_scoring_qualification_expires_at",
        "2020-01-01T00:00:00Z",
    )

    eligibility = scoring_provider.openai_route_eligibility(
        configured,
        route=route,
        user_id=USER_A,
        actual_fingerprint=route.qualification_fingerprint,
    )

    assert not eligibility.allowed
    assert eligibility.reason == "qualification_expired"


def test_serialized_route_is_frozen_against_later_config_changes() -> None:
    original = scoring_provider.route_for_session(
        settings(),
        user_id=USER_A,
        scoring_contract_version=2,
    )
    frozen = original.as_json()
    changed = settings(
        scoring_model="claude-new",
        scoring_effort="high",
        openai_v2_scoring_mode="shadow",
        openai_v2_scoring_effort="max",
        openai_v2_scoring_qualification_fingerprint="cd" * 32,
    )

    restored = scoring_provider.ScoringRoute.from_json(frozen, changed)

    assert restored == original
    assert restored.as_json() == frozen

    anthropic_settings = settings()
    anthropic = scoring_provider.anthropic_route(anthropic_settings)
    assert scoring_provider.ScoringRoute.from_json(
        anthropic.as_json(), anthropic_settings
    ) == anthropic


@pytest.mark.parametrize("legacy", [None, {}])
def test_legacy_session_without_a_frozen_route_falls_back_to_anthropic(
    legacy: dict[str, Any] | None,
) -> None:
    configured = settings(scoring_model="current-claude", scoring_effort="high")

    assert scoring_provider.ScoringRoute.from_json(
        legacy, configured
    ) == scoring_provider.ScoringRoute(
        mode="anthropic",
        anthropic_model="current-claude",
        anthropic_effort="high",
    )


@pytest.mark.parametrize(
    "value",
    [
        {"format_version": 2, "mode": "anthropic", "anthropic_model": "claude"},
        {"format_version": 1, "mode": "unknown", "anthropic_model": "claude"},
        {"format_version": 1, "mode": "anthropic"},
        {"mode": "anthropic", "anthropic_model": "claude"},
        "not-an-object",
    ],
)
def test_invalid_frozen_routes_fail_closed(value: Any) -> None:
    with pytest.raises(ValueError, match="invalid frozen scoring route"):
        scoring_provider.ScoringRoute.from_json(value, settings())


def test_partial_or_tampered_openai_route_fails_closed() -> None:
    configured = settings()
    frozen = scoring_provider.route_for_session(
        configured,
        user_id=USER_A,
        scoring_contract_version=2,
    ).as_json()
    changes = (
        {"anthropic_model": ""},
        {"openai_model": ""},
        {"openai_effort": 1},
        {"openai_effort": "unsupported"},
        {"qualification_fingerprint": "0" * 63},
        {"qualification_fingerprint": "AB" * 32},
        {"unexpected": "field"},
    )

    for change in changes:
        with pytest.raises(ValueError, match="invalid frozen scoring route"):
            scoring_provider.ScoringRoute.from_json({**frozen, **change}, configured)

    with pytest.raises(ValueError, match="invalid frozen scoring route"):
        scoring_provider.ScoringRoute.from_json([], configured)  # type: ignore[arg-type]


COMPLETION = {
    "model": "gpt-qualified",
    "effort": "low",
    "rubric": "Recall-only rubric",
    "user_content": "QUESTION: why?\nANSWER: because",
    "schema": {
        "type": "object",
        "properties": {"recall_score": {"type": "integer"}},
        "required": ["recall_score"],
        "additionalProperties": False,
    },
    "max_tokens": 2048,
    "retry": False,
}


@pytest.mark.parametrize(
    ("field", "replacement"),
    [
        ("model", "gpt-other"),
        ("effort", "medium"),
        ("rubric", "Changed rubric"),
        (
            "schema",
            {
                "type": "object",
                "properties": {"score": {"type": "integer"}},
                "required": ["score"],
                "additionalProperties": False,
            },
        ),
        ("max_tokens", 1024),
    ],
)
def test_qualification_fingerprint_changes_with_every_static_contract_input(
    field: str, replacement: object
) -> None:
    changed = {**COMPLETION, field: replacement}

    assert scoring_provider.qualification_fingerprint(
        changed
    ) != scoring_provider.qualification_fingerprint(COMPLETION)


def test_qualification_fingerprint_excludes_dynamic_user_content_and_transport_flags() -> None:
    dynamic_change = {
        **COMPLETION,
        "user_content": "QUESTION: a different card\nANSWER: a different learner answer",
        "retry": True,
        "purpose": "score_v2",
    }

    assert scoring_provider.qualification_fingerprint(
        dynamic_change
    ) == scoring_provider.qualification_fingerprint(COMPLETION)


@pytest.mark.parametrize(
    "version_name",
    ["V2_PARSER_POLICY_VERSION", "PRODUCT_DECISION_POLICY_VERSION"],
)
def test_qualification_fingerprint_changes_with_response_policy_versions(
    monkeypatch: pytest.MonkeyPatch, version_name: str
) -> None:
    original = scoring_provider.qualification_fingerprint(COMPLETION)
    monkeypatch.setattr(
        scoring_provider,
        version_name,
        getattr(scoring_provider, version_name) + 1,
    )

    assert scoring_provider.qualification_fingerprint(COMPLETION) != original


def test_current_v2_parser_policy_version_records_candidate_tolerance() -> None:
    assert scoring_provider.V2_PARSER_POLICY_VERSION == 2


def test_qualification_fingerprint_is_a_stable_sha256_digest() -> None:
    first = scoring_provider.qualification_fingerprint(COMPLETION)
    reordered_schema = {
        **COMPLETION,
        "schema": {
            "additionalProperties": False,
            "required": ["recall_score"],
            "properties": {"recall_score": {"type": "integer"}},
            "type": "object",
        },
    }

    assert len(first) == 64
    assert first == "9c49b518396dba543bdcf346f620313065c384184b0df1dd83ad1d2c7e193bf3"
    assert first == scoring_provider.qualification_fingerprint(reordered_schema)
    assert first == scoring_provider.qualification_fingerprint(COMPLETION)


def test_safety_identifier_is_the_expected_stable_hmac() -> None:
    configured = settings()
    expected = hmac.new(SAFETY_SECRET.encode(), USER_A.bytes, hashlib.sha256).hexdigest()

    identifier = scoring_provider.safety_identifier(configured, USER_A)

    assert identifier == expected
    assert len(identifier) == 64
    assert identifier == scoring_provider.safety_identifier(configured, USER_A)
    assert identifier != scoring_provider.safety_identifier(configured, USER_B)
    assert identifier != scoring_provider.safety_identifier(
        settings(openai_safety_identifier_secret="a different safety secret of sufficient length"),
        USER_A,
    )


DECISIONS = {
    0: ("again", "cold", "cold"),
    1: ("again", "cold", "cold"),
    2: ("again", "shaky", "shaky"),
    3: ("good", "shaky", "developing"),
    4: ("good", "solid", "solid"),
    5: ("good", "solid", "solid"),
}


@pytest.mark.parametrize(("recall", "expected"), DECISIONS.items())
def test_every_recall_value_maps_to_all_product_decisions(
    recall: int, expected: tuple[str, str, str]
) -> None:
    scheduler, today_band, coverage_tier = expected

    assert scoring_provider.product_decisions(status="complete", recall=recall) == {
        "flow": "complete",
        "scheduler": scheduler,
        "today_band": today_band,
        "coverage_tier": coverage_tier,
    }


@pytest.mark.parametrize(
    ("status", "recall"),
    [("follow_up", None), ("follow_up", 4), ("complete", None)],
)
def test_unscored_flow_has_no_numeric_product_decisions(
    status: str, recall: int | None
) -> None:
    assert scoring_provider.product_decisions(status=status, recall=recall) == {
        "flow": status
    }


@pytest.mark.parametrize(
    ("authoritative", "candidate"), list(itertools.product(range(6), repeat=2))
)
def test_shadow_comparison_covers_every_recall_pair(
    authoritative: int, candidate: int
) -> None:
    comparison = scoring_provider.compare_shadow_results(
        authoritative_status="complete",
        authoritative_recall=authoritative,
        candidate_status="complete",
        candidate_recall=candidate,
    )

    assert comparison.within_one is (abs(authoritative - candidate) <= 1)
    assert comparison.behavioral_match is (DECISIONS[authoritative] == DECISIONS[candidate])
    assert comparison.authoritative_decisions["scheduler"] == DECISIONS[authoritative][0]
    assert comparison.candidate_decisions["scheduler"] == DECISIONS[candidate][0]


@pytest.mark.parametrize(
    (
        "authoritative_status",
        "authoritative_recall",
        "candidate_status",
        "candidate_recall",
        "within_one",
        "behavioral_match",
    ),
    [
        ("follow_up", None, "follow_up", None, False, True),
        ("follow_up", None, "complete", 0, False, False),
        ("complete", None, "complete", None, False, True),
        ("complete", 3, "follow_up", None, False, False),
    ],
)
def test_shadow_comparison_covers_unscored_flow_boundaries(
    authoritative_status: str,
    authoritative_recall: int | None,
    candidate_status: str,
    candidate_recall: int | None,
    within_one: bool,
    behavioral_match: bool,
) -> None:
    comparison = scoring_provider.compare_shadow_results(
        authoritative_status=authoritative_status,
        authoritative_recall=authoritative_recall,
        candidate_status=candidate_status,
        candidate_recall=candidate_recall,
    )

    assert comparison.within_one is within_one
    assert comparison.behavioral_match is behavioral_match


def test_scoring_trace_expands_one_usage_record_per_physical_call() -> None:
    comparison = scoring_provider.compare_shadow_results(
        authoritative_status="complete",
        authoritative_recall=4,
        candidate_status="complete",
        candidate_recall=5,
    )
    trace = scoring_provider.ScoringTrace(
        route="shadow",
        authoritative_provider="anthropic",
        qualification_fingerprint=FINGERPRINT,
        calls=(
            scoring_provider.ProviderCallTrace(provider="anthropic", model="claude"),
            scoring_provider.ProviderCallTrace(
                provider="openai",
                model="gpt",
                response_id="resp_123",
                input_tokens=100,
                output_tokens=20,
            ),
        ),
        shadow=comparison,
    )

    details = trace.usage_details()

    assert len(details) == 2
    assert [row["call"]["provider"] for row in details] == ["anthropic", "openai"]
    assert all(row["route"] == "shadow" for row in details)
    assert all(row["shadow"]["behavioral_match"] is True for row in details)
