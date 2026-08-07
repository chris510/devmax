from functools import lru_cache

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Values that ship in this repo, in .env.example, or in the docs. They are public,
# so a deploy that reaches production still carrying one is not authenticated at all.
PLACEHOLDER_SECRETS = frozenset(
    {"dev-api-key", "dev-cron-secret", "change-me", "change-me-too", ""}
)


class Settings(BaseSettings):
    """Environment configuration. See spec.md §Environment variables."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # No defaults on the three that gate access to the database and the API. A default
    # here means a deploy that forgets to set them boots healthy on a public
    # hostname, authenticated by a string published in this repo.
    database_url: str
    api_key: str
    cron_secret: str

    anthropic_api_key: str = ""
    # Per-function model config so either can be swapped during calibration.
    scoring_model: str = "claude-sonnet-5"
    question_model: str = "claude-haiku-4-5"
    # Sonnet 5 runs adaptive thinking by default; effort bounds it so a 1-3 minute
    # session doesn't stall on scoring. Haiku 4.5 rejects `effort` entirely, hence
    # None — set this if you swap the question model to a 4.6+ model.
    #
    # "low" beat "medium" on both axes in a live sweep (scripts/effort_sweep.py,
    # 8 cases, 2026-07): 1460 vs 2353 output tokens, and 6/8 vs 3/8 exact score
    # matches. Against a rubric this crisp, more thinking produced *less*
    # consistent grading — medium's errors were scattered where low's were not.
    # Re-run the sweep before changing this; it's the product's core signal.
    scoring_effort: str | None = "low"
    question_effort: str | None = None

    # Turn 3, the coached re-attempt. Mirrors the scoring model deliberately — it
    # grades the same axis against the same transcript, so a weaker model would
    # write a mastery summary the scoring call then reads as peer evidence.
    #
    # UNSWEPT. `scoring_effort`'s "low" was chosen from a live sweep; this one is
    # inherited, not measured. The task is narrower than scoring (one axis, no
    # composite, no probe), so "low" is the right prior — but run
    # scripts/effort_sweep.py against this prompt before treating it as settled.
    reattempt_model: str = "claude-sonnet-5"
    reattempt_effort: str | None = "low"

    # Guide import. One call per plan creation, over a whole study guide, producing
    # a hundred structured items with source offsets — the hardest extraction in
    # the product and the one where a wrong answer is most expensive, because the
    # user reviews the result once and then lives inside it for twelve weeks.
    # Opus 5 at high effort; there is no latency budget here the way there is in a
    # 90-second review session, and the call happens roughly once a quarter.
    studyplan_model: str = "claude-opus-5"
    studyplan_effort: str | None = "high"

    # Card proposals from a completed plan item. Mirrors the scoring model, which
    # is what will grade the resulting cards — a stronger model here would write
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

    # The production API is a single always-on Railway replica. Keeping the dumb
    # trigger-review poll in that process removes an unreliable external schedule;
    # the endpoint still owns every notification-time decision. Disabled by
    # default so a local uvicorn never sends a real push from api/.env.
    review_poller_enabled: bool = False
    review_poll_interval_seconds: int = Field(default=15 * 60, ge=60, le=30 * 60)

    log_level: str = "INFO"

    @model_validator(mode="after")
    def _reject_placeholder_secrets(self) -> "Settings":
        for name in ("api_key", "cron_secret"):
            if getattr(self, name) in PLACEHOLDER_SECRETS:
                raise ValueError(
                    f"{name.upper()} is unset or still a placeholder. Generate one with "
                    "`openssl rand -base64 32`."
                )
        # spec.md §Auth: two *independent* shared secrets. Collapsing them into one
        # means the cron secret ships inside the iOS binary along with the API key.
        if self.api_key == self.cron_secret:
            raise ValueError("API_KEY and CRON_SECRET must be different values.")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
