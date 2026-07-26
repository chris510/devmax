"""Config and connection-URL handling.

Both are pure and both are load-bearing at deploy time: the first stops the app
booting with the secrets published in this repo, the second is the difference
between connecting to Neon and not.
"""

import pytest
from pydantic import ValidationError

from app.config import Settings
from app.db import _engine_kwargs

GOOD = dict(database_url="postgresql+asyncpg://u:p@host/db", api_key="realA", cron_secret="realB")


def build(**overrides) -> Settings:
    # _env_file=None so a developer's real .env can't make these pass locally.
    return Settings(_env_file=None, **{**GOOD, **overrides})


def test_a_full_config_boots() -> None:
    assert build().api_key == "realA"


@pytest.mark.parametrize("missing", ["database_url", "api_key", "cron_secret"])
def test_the_three_required_settings_have_no_default(
    monkeypatch: pytest.MonkeyPatch, missing: str
) -> None:
    """A default here means a deploy that forgets `fly secrets set` boots healthy."""
    # conftest exports all three so the app can import; drop the one under test.
    monkeypatch.delenv(missing.upper(), raising=False)
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **{k: v for k, v in GOOD.items() if k != missing})


@pytest.mark.parametrize(
    ("api_key", "cron_secret"),
    [
        ("dev-api-key", "realB"),  # the config.py defaults this replaced
        ("realA", "dev-cron-secret"),
        ("change-me", "realB"),  # .env.example, copied verbatim
        ("realA", "change-me-too"),
        ("", "realB"),
        ("realA", ""),
    ],
)
def test_placeholder_secrets_are_refused(api_key: str, cron_secret: str) -> None:
    with pytest.raises(ValidationError):
        build(api_key=api_key, cron_secret=cron_secret)


def test_the_two_secrets_must_differ() -> None:
    """spec.md §Auth calls for two independent secrets.

    Collapsing them ships the cron secret inside the iOS binary along with the
    API key.
    """
    with pytest.raises(ValidationError):
        build(api_key="same", cron_secret="same")


NEON = "postgresql+asyncpg://u:p@ep-x.aws.neon.tech/wc?sslmode=require&channel_binding=require"


def test_libpq_only_params_are_stripped_from_a_neon_url() -> None:
    """asyncpg rejects sslmode/channel_binding, and Neon's console emits both."""
    url, _ = _engine_kwargs(NEON)
    assert "sslmode" not in url
    assert "channel_binding" not in url


def test_neon_gets_tls_and_a_disabled_statement_cache() -> None:
    url, kwargs = _engine_kwargs(NEON)
    assert kwargs["connect_args"]["ssl"] is not None
    # PgBouncer in transaction mode on the pooled endpoint is incompatible with
    # asyncpg's prepared statements.
    assert kwargs["connect_args"]["statement_cache_size"] == 0
    assert "prepared_statement_cache_size=0" in url
    assert kwargs["pool_recycle"] == 300


@pytest.mark.parametrize(
    "url",
    [
        "postgresql+asyncpg://postgres@127.0.0.1:5432/wc",
        "postgresql+asyncpg://postgres@localhost/wc",
    ],
)
def test_a_local_cluster_is_not_forced_onto_tls(url: str) -> None:
    _, kwargs = _engine_kwargs(url)
    assert "ssl" not in kwargs["connect_args"]


def test_an_explicit_sslmode_wins_over_the_host_heuristic() -> None:
    _, kwargs = _engine_kwargs("postgresql+asyncpg://postgres@127.0.0.1/wc?sslmode=require")
    assert "ssl" in kwargs["connect_args"]


def test_sqlite_is_left_alone() -> None:
    """asyncpg's connect keywords make SQLite's connect() throw."""
    url, kwargs = _engine_kwargs("sqlite+aiosqlite:///:memory:")
    assert url == "sqlite+aiosqlite:///:memory:"
    assert kwargs["connect_args"] == {}
