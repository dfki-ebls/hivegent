"""Alembic environment for Hivegent.

Shared by the ``alembic`` CLI (revision generation) and the runtime
``apply_migrations`` helper.  The CLI reads its options from
``[tool.alembic]`` in ``backend/pyproject.toml``; the runtime helper
builds an equivalent ``Config`` in code.  Both flows pull the DB URL
from :func:`hivegent.db.engine.resolve_database_url`, so dev, tests,
and prod hit the database the rest of the app uses.
"""

import asyncio

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from hivegent.db.engine import resolve_database_url
from hivegent.db.models import Base

config = context.config
config.set_main_option("sqlalchemy.url", resolve_database_url())


def _run_migrations(connection: Connection) -> None:
    # ``begin_transaction`` and ``run_migrations`` must share this sync
    # callback: a failed migration rolls back here, inside the greenlet
    # where sync IO on the async connection is legal.  Splitting them
    # leaves the rollback stranded outside the greenlet, raising a
    # ``MissingGreenlet`` that masks the original migration error.
    context.configure(
        connection=connection,
        target_metadata=Base.metadata,
        compare_type=True,
        compare_server_default=True,
    )
    with context.begin_transaction():
        context.run_migrations()


async def _run() -> None:
    engine = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with engine.connect() as connection:
        await connection.run_sync(_run_migrations)
    await engine.dispose()


asyncio.run(_run())
