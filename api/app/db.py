import ssl
from collections.abc import AsyncIterator
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from sqlalchemy import event
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlmodel.ext.asyncio.session import AsyncSession

from app.config import get_settings

_settings = get_settings()

# libpq query parameters that asyncpg does not understand. A hosted provider will
# hand you a URL ending in something like `?sslmode=require&channel_binding=require`,
# and SQLAlchemy forwards unknown query params straight into asyncpg.connect(), which
# rejects them outright. Strip them and express the intent through connect_args.
_LIBPQ_ONLY_PARAMS = {"sslmode", "channel_binding", "options", "target_session_attrs"}

# Loopback only. An unrecognised host is assumed to be a real database, so this fails
# towards "don't touch it" rather than away from it.
_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1", ""}

# Networks where the transport is already encrypted, so app-level TLS adds nothing.
# Railway puts every service in an environment on an encrypted WireGuard mesh and
# addresses it as `<service>.railway.internal`.
_TRUSTED_SUFFIXES = (".railway.internal",)


def _host(url: str) -> str:
    return (urlsplit(url).hostname or "").lower()


def is_local_database(url: str) -> bool:
    """Whether the URL points at a developer's own machine.

    Deliberately narrower than `_on_trusted_network`: this is what keeps
    `seed.py --fixtures` away from a real database, and a private-network address
    like `postgres.railway.internal` is very much a real database.
    """
    return _host(url) in _LOCAL_HOSTS


def _on_trusted_network(url: str) -> bool:
    host = _host(url)
    return host in _LOCAL_HOSTS or host.endswith(_TRUSTED_SUFFIXES)


def _ssl_argument(sslmode: str | None, url: str) -> ssl.SSLContext | None:
    """The asyncpg `ssl` value, following libpq's own sslmode semantics.

    The distinction that matters: `require` means *encrypt*, it does not mean
    *verify* — only `verify-ca` and `verify-full` ask for certificate validation.
    Treating `require` as verifying is stricter than the URL asked for, and it
    breaks any provider fronting Postgres with a self-signed certificate, which is
    what Railway's TCP proxy does.

    With no sslmode given, a trusted network gets plaintext and anything else gets
    full verification — the safe default for a hosted database reached over the
    public internet.
    """
    if sslmode in {"disable", "allow"}:
        return None
    if sslmode is None:
        return None if _on_trusted_network(url) else ssl.create_default_context()

    context = ssl.create_default_context()
    if sslmode in {"verify-ca", "verify-full"}:
        return context
    # require / prefer: encrypted, unverified.
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    return context


def engine_kwargs(url: str) -> tuple[str, dict]:
    """Normalise a database URL and derive the engine arguments that go with it.

    Shared by the app engine below and by alembic/env.py — migrations run against
    the same URL through Railway's preDeployCommand, so they need the same treatment
    or `alembic upgrade head` fails where the app would have connected.
    """
    if "asyncpg" not in url:
        return url, {"connect_args": {}}

    parts = urlsplit(url)
    pairs = parse_qsl(parts.query, keep_blank_values=True)
    sslmode = next((v for k, v in pairs if k == "sslmode"), None)

    kept = [(k, v) for k, v in pairs if k not in _LIBPQ_ONLY_PARAMS]
    # SQLAlchemy's own prepared-statement cache — a separate knob from asyncpg's
    # statement_cache_size below, and one the dialect only reads off the URL.
    kept.append(("prepared_statement_cache_size", "0"))
    url = urlunsplit(parts._replace(query=urlencode(kept)))

    connect_args: dict = {
        # A database that sleeps when idle makes the first query after a quiet spell
        # pay a cold start.
        "timeout": 30,
        "command_timeout": 30,
        # A PgBouncer-style pooler in transaction mode is incompatible with asyncpg's
        # prepared-statement cache. Disabling it costs nothing at this traffic level
        # and makes a pooled endpoint survivable rather than silently broken.
        "statement_cache_size": 0,
    }
    context = _ssl_argument(sslmode, url)
    if context is not None:
        connect_args["ssl"] = context

    return url, {
        "connect_args": connect_args,
        # Idle connections get dropped by most hosted providers; recycle first.
        "pool_recycle": 300,
    }


_url, _kwargs = engine_kwargs(_settings.database_url)

engine = create_async_engine(_url, echo=False, pool_pre_ping=True, **_kwargs)

# SQLite leaves foreign-key enforcement off unless each connection opts in.
# Production is Postgres, but local development and tests must give account
# deletion the same cascade semantics rather than retaining orphaned user data.
if _url.startswith("sqlite"):

    @event.listens_for(engine.sync_engine, "connect")
    def _enable_sqlite_foreign_keys(dbapi_connection, _connection_record) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()


session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def get_session() -> AsyncIterator[AsyncSession]:
    async with session_factory() as session:
        yield session
