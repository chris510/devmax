import re
import string
import uuid
from datetime import UTC, datetime, timedelta
from functools import lru_cache
from typing import Literal

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.consent_policy import (
    DEFAULT_REQUIRED_POLICY_VERSION,
    LATEST_POLICY_VERSION,
    POLICIES_BY_VERSION,
)

# Values that ship in this repo, in .env.example, or in the docs. They are public,
# so a deploy that reaches production still carrying one is not authenticated at all.
PLACEHOLDER_SECRETS = frozenset(
    {"dev-api-key", "dev-cron-secret", "change-me", "change-me-too", ""}
)
MIN_FOUNDER_CLAIM_TOKEN_CHARS = 43
MIN_SHARED_SECRET_CHARS = 32
OPENAI_V2_LUNA_MODEL = "gpt-5.6-luna"
QUALIFICATION_MAX_AGE_DAYS = 30
_STRICT_UTC_TIMESTAMP = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|\+00:00)$"
)


def parse_strict_utc_timestamp(value: str, *, field: str) -> datetime:
    """Parse one explicit ISO-8601 UTC instant without accepting local offsets."""
    normalized = value.strip()
    if not _STRICT_UTC_TIMESTAMP.fullmatch(normalized):
        raise ValueError(
            f"{field} must be an ISO-8601 UTC timestamp ending in Z or +00:00."
        )
    try:
        instant = datetime.fromisoformat(normalized.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{field} must be a valid UTC timestamp.") from exc
    if instant.utcoffset() != UTC.utcoffset(instant):  # defensive; regex is stricter
        raise ValueError(f"{field} must use UTC, not a local timezone offset.")
    return instant.astimezone(UTC)


def qualification_expiry_within_max_age(
    expires_at: datetime, *, now: datetime
) -> bool:
    """Return whether an evidence deadline stays inside the shared 30-day cap."""
    if (
        expires_at.tzinfo is None
        or expires_at.utcoffset() is None
        or now.tzinfo is None
        or now.utcoffset() is None
    ):
        raise ValueError("qualification expiry bounds require timezone-aware instants")
    return expires_at.astimezone(UTC) <= now.astimezone(UTC) + timedelta(
        days=QUALIFICATION_MAX_AGE_DAYS
    )


class Settings(BaseSettings):
    """Environment configuration. See spec.md §Environment variables."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # No defaults on settings that gate access or provider-data disclosure. A default
    # here means a deploy that forgets to set them boots healthy on a public
    # hostname, authenticated by a string published in this repo.
    database_url: str
    api_key: str
    cron_secret: str

    # Temporary credential for the one-time founder Apple-identity claim. It is
    # deliberately separate from API_KEY, which already ships in the installed
    # compatibility build. Empty disables the claim route; remove it from the
    # deployment after the returned bearer credentials are verified on-device.
    founder_claim_token: str = ""
    # Transitional escape hatch for the already-installed private build. Turn
    # on explicitly only for that rollout window, then turn it off immediately
    # after the founder's bearer-token build is verified.
    legacy_api_key_auth_enabled: bool = False

    anthropic_api_key: str = ""
    # Per-function model config so either can be swapped during calibration.
    scoring_model: str = "claude-sonnet-5"
    question_model: str = "claude-haiku-4-5"
    # Sonnet 5 runs adaptive thinking by default; effort bounds it so a 1-3 minute
    # session doesn't stall on scoring. Haiku 4.5 rejects `effort` entirely, hence
    # None. Set this if you swap the question model to a 4.6+ model.
    #
    # "low" beat "medium" on both axes in a live sweep (scripts/effort_sweep.py,
    # 8 cases, 2026-07): 1460 vs 2353 output tokens, and 6/8 vs 3/8 exact score
    # matches. Against a rubric this crisp, more thinking produced *less*
    # consistent grading. Medium's errors were scattered where low's were not.
    # A source-grounded 18-case Week 1 sweep on 2026-08-07 confirmed the choice:
    # low had 13/18 exact composites at 4068 output tokens versus medium's 12/18
    # at 5029, with no false Accuracy passes or failures at either level.
    # Re-run the sweep before changing this; it's the product's core signal.
    scoring_effort: str | None = "low"
    # V2-capable code ships dark until a compatible iOS build is deployed.
    # Activation is one Railway variable, with V1 as the immediate rollback.
    scoring_contract_version: Literal[1, 2] = 1
    question_effort: str | None = None

    # OpenAI is a separately qualified V2 scoring transport. `off` is the
    # default and the production kill switch; V1 and every non-scoring workload
    # remain on Anthropic regardless of these values. A session snapshots the
    # selected route so a follow-up cannot change provider halfway through.
    openai_api_key: str = ""
    openai_v2_scoring_model: str = OPENAI_V2_LUNA_MODEL
    openai_v2_scoring_effort: Literal[
        "none", "low", "medium", "high", "xhigh", "max"
    ] = "low"
    openai_v2_scoring_mode: Literal["off", "shadow", "primary"] = "off"
    # Comma-separated UUIDs. Provider routing is server-owned; the client never
    # supplies a provider or opts itself into the canary.
    openai_v2_scoring_user_ids: str = ""
    # SHA-256 over the exact model/effort/prompt/schema/output-cap contract. The
    # evaluation runner prints it; any code/config change routes back to Claude
    # until the new fingerprint has passed qualification.
    openai_v2_scoring_qualification_fingerprint: str = ""
    # Explicit evidence deadline printed by the Stage 3 comparator. It is
    # required for every enabled mode; old evidence cannot authorize traffic
    # indefinitely merely because its request fingerprint still matches.
    openai_v2_scoring_qualification_expires_at: str = ""
    # A predeclared UUID scopes one no-replacement production shadow sample.
    # Its durable event ordinals start at one and cannot be restarted by an
    # export filter. Primary/off do not consume this setting.
    openai_v2_scoring_shadow_stage_id: str = ""
    # HMAC key for OpenAI's stable privacy-preserving safety_identifier. Keep it
    # independent from API/auth credentials so it can rotate on its own.
    openai_safety_identifier_secret: str = ""

    # Turn 3, the coached re-attempt. Mirrors the scoring model deliberately: it
    # grades the same axis against the same transcript, so a weaker model would
    # write a mastery summary the scoring call then reads as peer evidence.
    #
    # A 12-case source-grounded sweep on 2026-08-07 retained low provisionally.
    # Both levels were within one point on 11/12 cases and each had one false
    # parrot pass; medium additionally produced one false reconstruction failure
    # and used 1412 output tokens versus low's 944. Every mastery summary at both
    # levels correctly said the performance was coached. Expand the evaluation
    # before treating the numeric rubric as settled.
    reattempt_model: str = "claude-sonnet-5"
    reattempt_effort: str | None = "low"

    # Guide import. One call per plan creation, over a whole study guide, producing
    # a hundred structured items with source offsets, the hardest extraction in
    # the product and the one where a wrong answer is most expensive, because the
    # user reviews the result once and then lives inside it for twelve weeks.
    # Opus 5 at high effort; there is no latency budget here the way there is in a
    # 90-second review session, and the call happens roughly once a quarter.
    studyplan_model: str = "claude-opus-5"
    studyplan_effort: str | None = "high"

    # Card proposals from a completed plan item. Mirrors the scoring model, which
    # is what will grade the resulting cards. A stronger model here would write
    # questions the scoring model then marks against a different standard.
    card_proposal_model: str = "claude-sonnet-5"
    card_proposal_effort: str | None = "low"

    apns_key_id: str = ""
    apns_team_id: str = ""
    apns_bundle_id: str = ""
    apns_private_key: str = ""
    apns_use_sandbox: bool = True

    # Sign in with Apple. Empty values keep the founder compatibility build
    # bootable, but /auth/apple returns service_unavailable until all are set.
    apple_client_id: str = ""
    apple_team_id: str = ""
    apple_key_id: str = ""
    apple_private_key: str = ""
    # Fernet key used only for Apple's revocation-capable refresh token at rest.
    # Generate with `python -c 'from cryptography.fernet import Fernet; ...'`.
    auth_encryption_key: str = ""
    access_token_ttl_minutes: int = Field(default=15, ge=5, le=60)
    refresh_token_ttl_days: int = Field(default=90, ge=1, le=365)

    # Launch guardrails for paid model work. These are deliberately simple
    # account-level budgets, not engagement limits or analytics.
    llm_calls_per_day: int = Field(default=200, ge=10, le=10_000)
    guide_imports_per_day: int = Field(default=3, ge=1, le=100)

    # Deploy the consent schema before turning this on. This controls whether a
    # missing grant blocks provider work; policy-version activation is the
    # separate setting below. It is required so a missing Railway variable
    # cannot silently turn enforcement off during a fresh deployment.
    ai_consent_enforcement_enabled: bool
    # Code may understand a newer disclosure before production requires it. Keep
    # this on the already-shipped client's policy during that compatibility
    # window, then activate the newer policy explicitly after its minimum iOS
    # build is distributed. A code deploy alone can therefore never advance the
    # consent contract again.
    ai_consent_required_policy_version: str = DEFAULT_REQUIRED_POLICY_VERSION

    # The production API is a single always-on Railway replica. Keeping the dumb
    # trigger-review poll in that process removes an unreliable external schedule;
    # the endpoint still owns every notification-time decision. Disabled by
    # default so a local uvicorn never sends a real push from api/.env.
    review_poller_enabled: bool = False
    review_poll_interval_seconds: int = Field(default=15 * 60, ge=60, le=30 * 60)

    log_level: str = "INFO"

    @field_validator("scoring_contract_version", mode="before")
    @classmethod
    def _parse_scoring_contract_version(cls, value: object) -> object:
        """Accept the integer literals when they arrive through text-only env vars."""
        if isinstance(value, str):
            normalized = value.strip()
            if normalized in {"1", "2"}:
                return int(normalized)
        return value

    @field_validator("ai_consent_required_policy_version")
    @classmethod
    def _known_ai_consent_policy_version(cls, value: str) -> str:
        normalized = value.strip()
        if normalized not in POLICIES_BY_VERSION:
            supported = ", ".join(POLICIES_BY_VERSION)
            raise ValueError(
                "AI_CONSENT_REQUIRED_POLICY_VERSION must be a supported policy "
                f"version: {supported}."
            )
        return normalized

    @model_validator(mode="after")
    def _reject_placeholder_secrets(self) -> "Settings":
        for name in ("api_key", "cron_secret"):
            secret = getattr(self, name)
            if secret in PLACEHOLDER_SECRETS:
                raise ValueError(
                    f"{name.upper()} is unset or still a placeholder. Generate one with "
                    "`openssl rand -base64 32`."
                )
            if len(secret) < MIN_SHARED_SECRET_CHARS or any(
                character.isspace() for character in secret
            ):
                raise ValueError(
                    f"{name.upper()} must contain at least "
                    f"{MIN_SHARED_SECRET_CHARS} non-whitespace characters."
                )
        # spec.md §Auth: two *independent* shared secrets. Collapsing them into one
        # means the cron secret ships inside the iOS binary along with the API key.
        if self.api_key == self.cron_secret:
            raise ValueError("API_KEY and CRON_SECRET must be different values.")
        if self.founder_claim_token:
            if (
                self.founder_claim_token in PLACEHOLDER_SECRETS
                or not self.founder_claim_token.strip()
            ):
                raise ValueError(
                    "FOUNDER_CLAIM_TOKEN is still a placeholder. Generate one with "
                    "`openssl rand -hex 32`."
                )
            if len(self.founder_claim_token) < MIN_FOUNDER_CLAIM_TOKEN_CHARS:
                raise ValueError(
                    "FOUNDER_CLAIM_TOKEN must contain at least 43 characters "
                    "(for example, 32 random bytes encoded as 64 hex characters)."
                )
            if self.founder_claim_token in (self.api_key, self.cron_secret):
                raise ValueError(
                    "FOUNDER_CLAIM_TOKEN must differ from API_KEY and CRON_SECRET."
                )
        if self.openai_v2_scoring_mode != "off":
            try:
                allowlisted = self.openai_v2_scoring_user_id_set
            except ValueError as exc:
                raise ValueError(str(exc)) from exc
            if self.scoring_contract_version != 2:
                raise ValueError(
                    "SCORING_CONTRACT_VERSION must already be 2 before OpenAI V2 "
                    "scoring can be enabled."
                )
            if self.ai_consent_required_policy_version != LATEST_POLICY_VERSION:
                raise ValueError(
                    "AI_CONSENT_REQUIRED_POLICY_VERSION must require the combined "
                    "Anthropic and OpenAI disclosure before OpenAI V2 scoring can "
                    "be enabled."
                )
            missing = [
                name
                for name, value in (
                    ("ANTHROPIC_API_KEY", self.anthropic_api_key.strip()),
                    ("SCORING_MODEL", self.scoring_model.strip()),
                    ("OPENAI_API_KEY", self.openai_api_key.strip()),
                    ("OPENAI_V2_SCORING_MODEL", self.openai_v2_scoring_model.strip()),
                    (
                        "OPENAI_V2_SCORING_QUALIFICATION_FINGERPRINT",
                        self.openai_v2_scoring_qualification_fingerprint.strip(),
                    ),
                    (
                        "OPENAI_V2_SCORING_QUALIFICATION_EXPIRES_AT",
                        self.openai_v2_scoring_qualification_expires_at.strip(),
                    ),
                    (
                        "OPENAI_SAFETY_IDENTIFIER_SECRET",
                        self.openai_safety_identifier_secret.strip(),
                    ),
                )
                if not value
            ]
            if missing:
                raise ValueError(
                    "OpenAI V2 scoring is enabled but required settings are missing: "
                    + ", ".join(missing)
                )
            if len(allowlisted) != 1:
                raise ValueError(
                    "OPENAI_V2_SCORING_USER_IDS must contain exactly one owner UUID "
                    "when OpenAI V2 scoring is enabled. Expansion requires a later "
                    "rollout decision."
                )
            if self.openai_v2_scoring_mode == "shadow":
                try:
                    normalized_stage_id = str(
                        uuid.UUID(self.openai_v2_scoring_shadow_stage_id)
                    )
                except (AttributeError, ValueError) as exc:
                    raise ValueError(
                        "OPENAI_V2_SCORING_SHADOW_STAGE_ID must be a predeclared "
                        "UUID when shadow mode is enabled."
                    ) from exc
                if normalized_stage_id != self.openai_v2_scoring_shadow_stage_id:
                    raise ValueError(
                        "OPENAI_V2_SCORING_SHADOW_STAGE_ID must use canonical UUID text."
                    )
            if self.openai_v2_scoring_model.strip() != OPENAI_V2_LUNA_MODEL:
                raise ValueError(
                    "OPENAI_V2_SCORING_MODEL must be the separately qualified "
                    f"Luna model {OPENAI_V2_LUNA_MODEL!r}."
                )
            for name, value in (
                ("ANTHROPIC_API_KEY", self.anthropic_api_key),
                ("OPENAI_API_KEY", self.openai_api_key),
            ):
                if not value.strip() or value.strip() in PLACEHOLDER_SECRETS:
                    raise ValueError(
                        f"{name} is unset or still a placeholder while OpenAI V2 "
                        "scoring is enabled."
                    )
            normalized_anthropic_key = self.anthropic_api_key.strip()
            normalized_openai_key = self.openai_api_key.strip()
            app_credentials = {
                self.api_key.strip(),
                self.cron_secret.strip(),
                self.founder_claim_token.strip(),
            }
            if normalized_anthropic_key in app_credentials:
                raise ValueError(
                    "ANTHROPIC_API_KEY must be independent from app, cron, and "
                    "founder credentials."
                )
            if normalized_openai_key in {
                *app_credentials,
                normalized_anthropic_key,
            }:
                raise ValueError(
                    "OPENAI_API_KEY must be independent from app, cron, founder, "
                    "and Anthropic credentials."
                )
            fingerprint = self.openai_v2_scoring_qualification_fingerprint
            if len(fingerprint) != 64 or any(
                character not in string.hexdigits for character in fingerprint
            ):
                raise ValueError(
                    "OPENAI_V2_SCORING_QUALIFICATION_FINGERPRINT must be a 64-character "
                    "SHA-256 hex digest."
                )
            try:
                qualification_expires_at = self.openai_v2_scoring_qualification_expiry
            except ValueError as exc:
                raise ValueError(str(exc)) from exc
            qualification_now = datetime.now(UTC)
            if qualification_expires_at <= qualification_now:
                raise ValueError(
                    "OPENAI_V2_SCORING_QUALIFICATION_EXPIRES_AT is expired; "
                    "keep OpenAI V2 scoring off until fresh evidence passes."
                )
            if not qualification_expiry_within_max_age(
                qualification_expires_at, now=qualification_now
            ):
                raise ValueError(
                    "OPENAI_V2_SCORING_QUALIFICATION_EXPIRES_AT cannot be more "
                    f"than {QUALIFICATION_MAX_AGE_DAYS} days in the future."
                )
            normalized_safety_secret = self.openai_safety_identifier_secret.strip()
            if normalized_safety_secret in PLACEHOLDER_SECRETS:
                raise ValueError(
                    "OPENAI_SAFETY_IDENTIFIER_SECRET is still a placeholder. Generate "
                    "at least 32 random bytes."
                )
            if len(normalized_safety_secret) < 32:
                raise ValueError(
                    "OPENAI_SAFETY_IDENTIFIER_SECRET must contain at least 32 characters."
                )
            if normalized_safety_secret in {
                self.api_key.strip(),
                self.cron_secret.strip(),
                self.founder_claim_token.strip(),
                normalized_anthropic_key,
                normalized_openai_key,
            }:
                raise ValueError(
                    "OPENAI_SAFETY_IDENTIFIER_SECRET must be independent from API, "
                    "provider, and cron credentials."
                )
            if not self.ai_consent_enforcement_enabled:
                raise ValueError(
                    "AI_CONSENT_ENFORCEMENT_ENABLED must be true before OpenAI V2 "
                    "scoring can transmit learner content."
                )
        return self

    @property
    def openai_v2_scoring_user_id_set(self) -> frozenset[uuid.UUID]:
        """Parse the deployment allowlist without accepting client input."""
        values = [value.strip() for value in self.openai_v2_scoring_user_ids.split(",")]
        try:
            return frozenset(uuid.UUID(value) for value in values if value)
        except ValueError as exc:
            raise ValueError(
                "OPENAI_V2_SCORING_USER_IDS must be a comma-separated list of UUIDs."
            ) from exc

    @property
    def openai_v2_scoring_qualification_expiry(self) -> datetime:
        """Return the deployed evidence deadline as a timezone-aware UTC instant."""
        return parse_strict_utc_timestamp(
            self.openai_v2_scoring_qualification_expires_at,
            field="OPENAI_V2_SCORING_QUALIFICATION_EXPIRES_AT",
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()
