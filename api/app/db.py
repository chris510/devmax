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


def _split_libpq_params(url: str) -> tuple[str, dict[str, str]]:
    """Return the URL with libpq-only params removed, plus the params that were removed."""
    parts = urlsplit(url)
    pairs = parse_qsl(parts.query, keep_blank_values=True)
    kept = [(k, v) for k, v in pairs if k not in _LIBPQ_ONLY_PARAMS]
    removed = {k: v for k, v in pairs if k in _LIBPQ_ONLY_PARAMS}
    return urlunsplit(parts._replace(query=urlencode(kept))), removed


_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1", ""}


def _wants_tls(sslmode: str | None, host: str | None) -> bool:
    """Neon requires TLS; a local dev cluster generally can't offer it.

    An explicit `sslmode` in the URL always wins, so pasting Neon's own connection
    string does the right thing even though the parameter itself has to be stripped.
    """
    if sslmode is not None:
        return sslmode not in {"disable", "allow"}
    return (host or "").lower() not in _LOCAL_HOSTS


def _with_query(url: str, **params: str) -> str:
    parts = urlsplit(url)
    pairs = parse_qsl(parts.query, keep_blank_values=True) + list(params.items())
    return urlunsplit(parts._replace(query=urlencode(pairs)))


def _engine_kwargs(url: str) -> tuple[str, dict]:
    if "asyncpg" not in url:
        return url, {"connect_args": {}}

    url, removed = _split_libpq_params(url)
    # SQLAlchemy's own prepared-statement cache — a separate knob from asyncpg's
    # statement_cache_size below, and one the dialect only reads off the URL.
    url = _with_query(url, prepared_statement_cache_size="0")
    connect_args: dict = {
        # Neon scales to zero, so the first query after idle pays a cold start.
        "timeout": 30,
        "command_timeout": 30,
        # Neon's pooled (`-pooler`) host runs PgBouncer in transaction mode, which is
        # incompatible with asyncpg's prepared-statement cache. Prefer the direct
        # endpoint; this makes the pooled one survivable rather than silently broken.
        "statement_cache_size": 0,
    }
    if _wants_tls(removed.get("sslmode"), urlsplit(url).hostname):
        connect_args["ssl"] = ssl.create_default_context()
    return url, {
        "connect_args": connect_args,
        # Neon drops idle connections; recycle before it does.
        "pool_recycle": 300,
    }


_url, _kwargs = _engine_kwargs(_settings.database_url)

engine = create_async_engine(_url, echo=False, pool_pre_ping=True, **_kwargs)

session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def get_session() -> AsyncIterator[AsyncSession]:
    async with session_factory() as session:
        yield session
