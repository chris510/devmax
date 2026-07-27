import asyncio
from logging.config import fileConfig

from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from alembic import context
from app import models  # noqa: F401  — registers tables on SQLModel.metadata
from app.config import get_settings
from app.db import engine_kwargs

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Deliberately None: every migration here is handwritten. SQLModel.metadata diverges
# from migration 0001 on purpose — the four CHECK constraints, every server_default,
# and TEXT/SMALLINT vs VARCHAR/INTEGER live only in the migration — so pointing
# autogenerate at it would emit a destructive revision that drops the constraints.
# Write revisions by hand and verify them against a real Postgres instance.
target_metadata = None


def run_migrations_offline() -> None:
    context.configure(
        url=get_settings().database_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def _run(connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    # Same URL normalisation the app uses: Neon's connection string carries
    # libpq-only parameters that asyncpg rejects, and Fly's release_command runs
    # `alembic upgrade head` against exactly that string. Without this, the first
    # deploy fails here — before any app machine starts.
    url, kwargs = engine_kwargs(get_settings().database_url)
    # One connection, no pool: this process exits as soon as the migration lands.
    engine = create_async_engine(url, poolclass=NullPool, **kwargs)
    async with engine.connect() as connection:
        await connection.run_sync(_run)
    await engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
