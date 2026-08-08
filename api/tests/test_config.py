"""Config and connection-URL handling.

Both are pure and both are load-bearing at deploy time: the first stops the app
booting with the secrets published in this repo, the second is the difference
between connecting to the database and not.
"""

import ssl

import pytest
from pydantic import ValidationError
from sqlalchemy import DateTime
from sqlmodel import SQLModel

from app import models  # noqa: F401  — registers tables on SQLModel.metadata
from app.config import Settings
from app.db import engine_kwargs

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
    """A default here means a deploy that forgets to set them boots healthy."""
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


# Railway's private mesh address, which is what ${{Postgres.DATABASE_URL}} resolves to.
RAILWAY_PRIVATE = "postgresql+asyncpg://postgres:p@postgres.railway.internal:5432/railway"
# The public TCP proxy, fronted by a self-signed certificate.
RAILWAY_PUBLIC = (
    "postgresql+asyncpg://postgres:p@metro.proxy.rlwy.net:41234/railway?sslmode=require"
)
HOSTED = "postgresql+asyncpg://u:p@db.example-cloud.com/wc?sslmode=require&channel_binding=require"


def test_libpq_only_params_are_stripped() -> None:
    """asyncpg rejects sslmode/channel_binding; hosted providers emit both."""
    url, _ = engine_kwargs(HOSTED)
    assert "sslmode" not in url
    assert "channel_binding" not in url


def test_a_hosted_database_gets_tls_and_a_disabled_statement_cache() -> None:
    url, kwargs = engine_kwargs(HOSTED)
    assert kwargs["connect_args"]["ssl"] is not None
    # A PgBouncer-style pooler in transaction mode is incompatible with asyncpg's
    # prepared statements.
    assert kwargs["connect_args"]["statement_cache_size"] == 0
    assert "prepared_statement_cache_size=0" in url
    assert kwargs["pool_recycle"] == 300


@pytest.mark.parametrize(
    "url",
    [
        "postgresql+asyncpg://postgres@127.0.0.1:5432/wc",
        "postgresql+asyncpg://postgres@localhost/wc",
        # Railway's private networking is an encrypted WireGuard mesh, so app-level
        # TLS buys nothing — and the image's certificate is self-signed, so demanding
        # it would fail outright.
        RAILWAY_PRIVATE,
    ],
)
def test_a_trusted_network_is_not_forced_onto_tls(url: str) -> None:
    _, kwargs = engine_kwargs(url)
    assert "ssl" not in kwargs["connect_args"]


def test_an_explicit_sslmode_wins_over_the_host_heuristic() -> None:
    _, kwargs = engine_kwargs("postgresql+asyncpg://postgres@127.0.0.1/wc?sslmode=require")
    assert "ssl" in kwargs["connect_args"]


@pytest.mark.parametrize("sslmode", ["require", "prefer"])
def test_require_encrypts_without_verifying(sslmode: str) -> None:
    """libpq semantics: `require` means encrypt, not validate.

    Only verify-ca/verify-full ask for validation. Treating `require` as verifying
    is stricter than the URL asked for, and it fails against any provider using a
    self-signed certificate — which is what Railway's TCP proxy does.
    """
    _, kwargs = engine_kwargs(f"postgresql+asyncpg://u:p@host.example.com/wc?sslmode={sslmode}")
    context = kwargs["connect_args"]["ssl"]

    assert context.verify_mode == ssl.CERT_NONE
    assert context.check_hostname is False


@pytest.mark.parametrize("sslmode", ["verify-ca", "verify-full"])
def test_verify_modes_validate_the_certificate(sslmode: str) -> None:
    _, kwargs = engine_kwargs(f"postgresql+asyncpg://u:p@host.example.com/wc?sslmode={sslmode}")

    assert kwargs["connect_args"]["ssl"].verify_mode == ssl.CERT_REQUIRED


def test_railways_public_proxy_connects_without_a_cert_error() -> None:
    """The self-signed certificate is why this needs ?sslmode=require, not a bare URL."""
    _, kwargs = engine_kwargs(RAILWAY_PUBLIC)

    assert kwargs["connect_args"]["ssl"].verify_mode == ssl.CERT_NONE


@pytest.mark.parametrize("sslmode", ["disable", "allow"])
def test_ssl_can_be_turned_off_outright(sslmode: str) -> None:
    _, kwargs = engine_kwargs(f"postgresql+asyncpg://u:p@host.example.com/wc?sslmode={sslmode}")

    assert "ssl" not in kwargs["connect_args"]


def test_an_unknown_remote_host_defaults_to_full_verification() -> None:
    """No sslmode and not a trusted network — the safe default over the internet."""
    _, kwargs = engine_kwargs("postgresql+asyncpg://u:p@db.example-cloud.com/wc")

    assert kwargs["connect_args"]["ssl"].verify_mode == ssl.CERT_REQUIRED


def test_sqlite_is_left_alone() -> None:
    """asyncpg's connect keywords make SQLite's connect() throw."""
    url, kwargs = engine_kwargs("sqlite+aiosqlite:///:memory:")
    assert url == "sqlite+aiosqlite:///:memory:"
    assert kwargs["connect_args"] == {}


def test_every_timestamp_column_is_timezone_aware() -> None:
    """Guards the property, not the four columns that currently have it.

    Migration 0001 creates every timestamp as `timestamptz` and the app always
    writes tz-aware values, but the asyncpg dialect casts bind parameters from the
    *model* type. A column that forgets `sa_type=TZ_DATETIME` emits
    `$n::TIMESTAMP WITHOUT TIME ZONE` and asyncpg rejects every insert into that
    table. SQLite silently drops tzinfo, so the default test path cannot catch it —
    this check runs there anyway because it reads metadata, not a database.
    """
    naive = [
        f"{table.name}.{column.name}"
        for table in SQLModel.metadata.tables.values()
        for column in table.columns
        if isinstance(column.type, DateTime) and not column.type.timezone
    ]

    assert naive == [], f"declare these with sa_type=TZ_DATETIME: {naive}"
