"""Deployment-safety tests for the separately qualified OpenAI V2 scorer."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from app import config as app_config
from app.config import QUALIFICATION_MAX_AGE_DAYS, Settings

GOOD = {
    "database_url": "postgresql+asyncpg://u:p@host/db",
    "api_key": "realA",
    "cron_secret": "realB",
}
USER_A = uuid.UUID("11111111-1111-4111-8111-111111111111")
USER_B = uuid.UUID("22222222-2222-4222-8222-222222222222")
FINGERPRINT = "ab" * 32
SAFETY_SECRET = "s" * 32
QUALIFICATION_EXPIRY = (datetime.now(UTC) + timedelta(days=1)).isoformat()
SHADOW_STAGE_ID = "33333333-3333-4333-8333-333333333333"
OPENAI_ENV_NAMES = (
    "OPENAI_API_KEY",
    "OPENAI_V2_SCORING_MODEL",
    "OPENAI_V2_SCORING_EFFORT",
    "OPENAI_V2_SCORING_MODE",
    "OPENAI_V2_SCORING_USER_IDS",
    "OPENAI_V2_SCORING_QUALIFICATION_FINGERPRINT",
    "OPENAI_V2_SCORING_QUALIFICATION_EXPIRES_AT",
    "OPENAI_V2_SCORING_SHADOW_STAGE_ID",
    "OPENAI_SAFETY_IDENTIFIER_SECRET",
)


def build(**overrides: object) -> Settings:
    return Settings(_env_file=None, **{**GOOD, **overrides})


def enabled(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "anthropic_api_key": "anthropic-test-key",
        "openai_api_key": "sk-test",
        "openai_v2_scoring_model": "gpt-5.6-luna",
        "openai_v2_scoring_effort": "low",
        "openai_v2_scoring_mode": "primary",
        "openai_v2_scoring_user_ids": str(USER_A),
        "openai_v2_scoring_qualification_fingerprint": FINGERPRINT,
        "openai_v2_scoring_qualification_expires_at": QUALIFICATION_EXPIRY,
        "openai_v2_scoring_shadow_stage_id": SHADOW_STAGE_ID,
        "openai_safety_identifier_secret": SAFETY_SECRET,
        "ai_consent_enforcement_enabled": True,
        "scoring_contract_version": 2,
    }
    values.update(overrides)
    return build(**values)


def test_openai_v2_scoring_is_off_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in OPENAI_ENV_NAMES:
        monkeypatch.delenv(name, raising=False)

    settings = build()

    assert settings.openai_v2_scoring_mode == "off"
    assert settings.openai_api_key == ""
    assert settings.openai_v2_scoring_model == "gpt-5.6-luna"
    assert settings.openai_v2_scoring_effort == "low"
    assert settings.openai_v2_scoring_user_id_set == frozenset()
    assert settings.openai_v2_scoring_qualification_fingerprint == ""
    assert settings.openai_v2_scoring_qualification_expires_at == ""
    assert settings.openai_v2_scoring_shadow_stage_id == ""
    assert settings.openai_safety_identifier_secret == ""


def test_off_mode_allows_empty_enablement_prerequisites() -> None:
    settings = build(
        openai_v2_scoring_mode="off",
        openai_api_key="",
        openai_v2_scoring_model="",
        openai_v2_scoring_user_ids="",
        openai_v2_scoring_qualification_fingerprint="",
        openai_v2_scoring_qualification_expires_at="",
        openai_safety_identifier_secret="",
        ai_consent_enforcement_enabled=False,
    )

    assert settings.openai_v2_scoring_mode == "off"


def test_off_kill_switch_ignores_an_arbitrarily_distant_expiry() -> None:
    settings = build(
        openai_v2_scoring_mode="off",
        openai_v2_scoring_qualification_expires_at="2999-01-01T00:00:00Z",
    )

    assert settings.openai_v2_scoring_mode == "off"


def test_allowlist_accepts_whitespace_and_deduplicates() -> None:
    settings = build(
        openai_v2_scoring_mode="off",
        openai_v2_scoring_user_ids=f" {USER_A}, {USER_B}, {USER_A} ",
    )

    assert settings.openai_v2_scoring_user_id_set == frozenset({USER_A, USER_B})


def test_off_kill_switch_boots_even_with_a_stale_malformed_allowlist() -> None:
    settings = build(
        openai_v2_scoring_mode="off",
        openai_v2_scoring_user_ids=f"{USER_A},not-a-uuid",
    )

    assert settings.openai_v2_scoring_mode == "off"


def test_malformed_allowlist_uuid_is_rejected_when_enabled() -> None:
    with pytest.raises(ValidationError, match="comma-separated list of UUIDs"):
        enabled(
            openai_v2_scoring_user_ids=f"{USER_A},not-a-uuid",
        )


@pytest.mark.parametrize(
    ("field", "message"),
    [
        ("anthropic_api_key", "ANTHROPIC_API_KEY"),
        ("scoring_model", "SCORING_MODEL"),
        ("openai_api_key", "OPENAI_API_KEY"),
        ("openai_v2_scoring_model", "OPENAI_V2_SCORING_MODEL"),
        (
            "openai_v2_scoring_qualification_fingerprint",
            "OPENAI_V2_SCORING_QUALIFICATION_FINGERPRINT",
        ),
        (
            "openai_v2_scoring_qualification_expires_at",
            "OPENAI_V2_SCORING_QUALIFICATION_EXPIRES_AT",
        ),
        ("openai_safety_identifier_secret", "OPENAI_SAFETY_IDENTIFIER_SECRET"),
    ],
)
def test_enabled_mode_rejects_missing_prerequisites(field: str, message: str) -> None:
    with pytest.raises(ValidationError, match=message):
        enabled(**{field: ""})


def test_enabled_mode_requires_a_nonempty_allowlist() -> None:
    with pytest.raises(ValidationError, match="exactly one owner UUID"):
        enabled(openai_v2_scoring_user_ids=" , ")


def test_enabled_mode_rejects_allowlist_expansion() -> None:
    with pytest.raises(ValidationError, match="exactly one owner UUID"):
        enabled(openai_v2_scoring_user_ids=f"{USER_A},{USER_B}")

    settings = enabled(openai_v2_scoring_user_ids=f"{USER_A},{USER_A}")
    assert settings.openai_v2_scoring_user_id_set == frozenset({USER_A})


@pytest.mark.parametrize(
    "stage_id", ["", "not-a-uuid", "AAAAAAAA-AAAA-4AAA-8AAA-AAAAAAAAAAAA"]
)
def test_shadow_mode_requires_a_canonical_predeclared_stage_uuid(
    stage_id: str,
) -> None:
    with pytest.raises(ValidationError, match="SHADOW_STAGE_ID"):
        enabled(
            openai_v2_scoring_mode="shadow",
            openai_v2_scoring_shadow_stage_id=stage_id,
        )


def test_primary_mode_does_not_require_a_shadow_stage_uuid() -> None:
    settings = enabled(
        openai_v2_scoring_mode="primary",
        openai_v2_scoring_shadow_stage_id="",
    )

    assert settings.openai_v2_scoring_shadow_stage_id == ""


def test_enabled_mode_allows_only_the_qualified_luna_model() -> None:
    with pytest.raises(ValidationError, match="separately qualified Luna model"):
        enabled(openai_v2_scoring_model="gpt-other")


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("anthropic_api_key", "change-me", "ANTHROPIC_API_KEY"),
        ("openai_api_key", "change-me-too", "OPENAI_API_KEY"),
        ("anthropic_api_key", GOOD["api_key"], "ANTHROPIC_API_KEY must be independent"),
        ("openai_api_key", GOOD["cron_secret"], "OPENAI_API_KEY must be independent"),
        ("openai_api_key", "anthropic-test-key", "OPENAI_API_KEY must be independent"),
    ],
)
def test_enabled_mode_requires_nonplaceholder_independent_provider_keys(
    field: str, value: str, message: str
) -> None:
    with pytest.raises(ValidationError, match=message):
        enabled(**{field: value})


@pytest.mark.parametrize("fingerprint", ["a" * 63, "a" * 65, "g" * 64])
def test_enabled_mode_requires_a_sha256_fingerprint(fingerprint: str) -> None:
    with pytest.raises(ValidationError, match="64-character SHA-256 hex digest"):
        enabled(openai_v2_scoring_qualification_fingerprint=fingerprint)


@pytest.mark.parametrize(
    "expires_at",
    [
        "2099-01-01",
        "2099-01-01T00:00:00",
        "2099-01-01T00:00:00-07:00",
        "2099-01-01T00:00:00+01:00",
        "not-a-timestamp",
    ],
)
def test_enabled_mode_requires_a_strict_utc_qualification_expiry(
    expires_at: str,
) -> None:
    with pytest.raises(ValidationError, match="ISO-8601 UTC timestamp"):
        enabled(openai_v2_scoring_qualification_expires_at=expires_at)


@pytest.mark.parametrize(
    "expires_at", ["2020-01-01T00:00:00Z", "2020-01-01T00:00:00+00:00"]
)
def test_enabled_mode_rejects_expired_qualification(expires_at: str) -> None:
    with pytest.raises(ValidationError, match="is expired"):
        enabled(openai_v2_scoring_qualification_expires_at=expires_at)


def test_enabled_expiry_accepts_exact_30_day_boundary_and_rejects_beyond(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixed_now = datetime(2026, 8, 14, 12, 0, tzinfo=UTC)

    class FixedDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed_now if tz is not None else fixed_now.replace(tzinfo=None)

    monkeypatch.setattr(app_config, "datetime", FixedDateTime)
    exact = fixed_now + timedelta(days=QUALIFICATION_MAX_AGE_DAYS)
    beyond = exact + timedelta(microseconds=1)

    accepted = enabled(
        openai_v2_scoring_qualification_expires_at=exact.isoformat()
    )
    assert accepted.openai_v2_scoring_qualification_expiry == exact
    with pytest.raises(ValidationError, match="cannot be more than 30 days"):
        enabled(openai_v2_scoring_qualification_expires_at=beyond.isoformat())


def test_enabled_mode_requires_a_long_independent_safety_secret() -> None:
    with pytest.raises(ValidationError, match="still a placeholder"):
        enabled(openai_safety_identifier_secret=" change-me ")

    with pytest.raises(ValidationError, match="at least 32 characters"):
        enabled(openai_safety_identifier_secret="short")

    shared = "same-provider-and-safety-secret-value"
    with pytest.raises(ValidationError, match="must be independent"):
        enabled(openai_api_key=shared, openai_safety_identifier_secret=shared)


def test_enabled_mode_requires_consent_enforcement() -> None:
    with pytest.raises(ValidationError, match="AI_CONSENT_ENFORCEMENT_ENABLED"):
        enabled(ai_consent_enforcement_enabled=False)


def test_enabled_mode_requires_v2_to_be_active_first() -> None:
    with pytest.raises(ValidationError, match="SCORING_CONTRACT_VERSION"):
        enabled(scoring_contract_version=1)


@pytest.mark.parametrize("mode", ["shadow", "primary"])
def test_fully_qualified_enabled_settings_are_valid(mode: str) -> None:
    settings = enabled(openai_v2_scoring_mode=mode)

    assert settings.openai_v2_scoring_mode == mode
    assert settings.openai_v2_scoring_user_id_set == frozenset({USER_A})
    assert settings.openai_v2_scoring_qualification_fingerprint == FINGERPRINT
    assert settings.ai_consent_enforcement_enabled is True
