from functools import lru_cache

from pydantic import model_validator
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

    apns_key_id: str = ""
    apns_team_id: str = ""
    apns_bundle_id: str = ""
    apns_private_key: str = ""
    apns_use_sandbox: bool = True

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
