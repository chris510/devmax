from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlmodel.ext.asyncio.session import AsyncSession

from app.config import get_settings

_settings = get_settings()

# Neon scales to zero, so the first query after idle pays a cold start — hence the
# generous timeouts. They're asyncpg keywords, so they only apply on that driver;
# SQLite's connect() rejects them outright.
_connect_args = (
    {"timeout": 30, "command_timeout": 30} if "asyncpg" in _settings.database_url else {}
)

engine = create_async_engine(
    _settings.database_url,
    echo=False,
    pool_pre_ping=True,
    connect_args=_connect_args,
)

session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def get_session() -> AsyncIterator[AsyncSession]:
    async with session_factory() as session:
        yield session
