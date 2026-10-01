"""Alembic entry point. The database URL comes from the same settings as the app."""

import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import create_async_engine

from gateway import usage  # noqa: F401  (importing a table module registers it on Base.metadata)
from gateway.config import Settings
from gateway.db import Base

if context.config.config_file_name is not None:
    # Keep loggers created before migrations ran (e.g. in tests) working.
    fileConfig(context.config.config_file_name, disable_existing_loggers=False)


def run_migrations(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=Base.metadata)
    with context.begin_transaction():
        context.run_migrations()


async def main() -> None:
    engine = create_async_engine(Settings().database_url, poolclass=pool.NullPool)
    async with engine.connect() as connection:
        await connection.run_sync(run_migrations)
    await engine.dispose()


asyncio.run(main())
