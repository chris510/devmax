import ssl
from collections.abc import AsyncIterator
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlmodel.ext.asyncio.session import AsyncSession

from app.config import get_settings

_settings = get_settings()

# libpq query parameters that asyncpg does not understand. Neon's console hands you
# a URL ending in `?sslmode=require&channel_binding=require`, and SQLAlchemy forwards
# unknown query params straight into asyncpg.connect(), which rejects them outright.
# Strip them and express the intent through connect_args instead.
_LIBPQ_ONLY_PARAMS = {"sslmode", "channel_binding", "options", "target_session_attrs"}

# Everything else is treated as remote — an allowlist, so an unrecognised host fails
# towards "this is production" rather than away from it.
_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1", ""}


def is_local_database(url: str) -> bool:
    """Whether the URL points at a local dev cluster rather than a hosted one.

    Used both to decide whether TLS is wanted and to keep `seed.py --fixtures`
    away from a real database.
    """
    return (urlsplit(url).hostname or "").lower() in _LOCAL_HOSTS


def _wants_tls(sslmode: str | None, url: str) -> bool:
    """A hosted database requires TLS; a local dev cluster generally can't offer it.

    An explicit `sslmode` in the URL always wins, so pasting Neon's own connection
    string does the right thing even though the parameter itself has to be stripped.
    """
    if sslmode is not None:
        return sslmode not in {"disable", "allow"}
    return not is_local_database(url)


def engine_kwargs(url: str) -> tuple[str, dict]:
    """Normalise a database URL and derive the engine arguments that go with it.

    Shared by the app engine below and by alembic/env.py — migrations run against
    the same Neon URL through Fly's release_command, so they need the same
    treatment or `alembic upgrade head` fails where the app would have connected.
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
        # Neon scales to zero, so the first query after idle pays a cold start.
        "timeout": 30,
        "command_timeout": 30,
        # Neon's pooled (`-pooler`) host runs PgBouncer in transaction mode, which is
        # incompatible with asyncpg's prepared-statement cache. Prefer the direct
        # endpoint; this makes the pooled one survivable rather than silently broken.
        "statement_cache_size": 0,
    }
    if _wants_tls(sslmode, url):
        connect_args["ssl"] = ssl.create_default_context()

    return url, {
        "connect_args": connect_args,
        # Neon drops idle connections; recycle before it does.
        "pool_recycle": 300,
    }


_url, _kwargs = engine_kwargs(_settings.database_url)

engine = create_async_engine(_url, echo=False, pool_pre_ping=True, **_kwargs)

session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def get_session() -> AsyncIterator[AsyncSession]:
    async with session_factory() as session:
        yield session
