from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Environment configuration. See spec.md §Environment variables."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+asyncpg://localhost/warmcache"
    api_key: str = "dev-api-key"
    cron_secret: str = "dev-cron-secret"

    anthropic_api_key: str = ""
    # Per-function model config so either can be swapped during calibration.
    scoring_model: str = "claude-sonnet-5"
    question_model: str = "claude-haiku-4-5"
    # Sonnet 5 runs adaptive thinking by default; effort bounds it so a 1-3 minute
    # session doesn't stall on scoring. Haiku 4.5 rejects `effort` entirely, hence
    # None — set this if you swap the question model to a 4.6+ model.
    scoring_effort: str | None = "medium"
    question_effort: str | None = None

    apns_key_id: str = ""
    apns_team_id: str = ""
    apns_bundle_id: str = ""
    apns_private_key: str = ""
    apns_use_sandbox: bool = True

    log_level: str = "INFO"


@lru_cache
def get_settings() -> Settings:
    return Settings()
