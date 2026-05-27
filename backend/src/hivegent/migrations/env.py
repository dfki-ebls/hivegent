"""Alembic environment for Hivegent.

Shared by the ``alembic`` CLI (revision generation) and the runtime
``apply_migrations`` helper.  The CLI reads its options from
``[tool.alembic]`` in ``backend/pyproject.toml``; the runtime helper
builds an equivalent ``Config`` in code.  Both flows pull the DB URL
from :func:`hivegent.db.engine.resolve_database_url`, so dev, tests,
and prod hit the database the rest of the app uses.
"""

from __future__ import annotations

import asyncio

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from hivegent.db.engine import resolve_database_url
from hivegent.db.models import Base

config = context.config
config.set_main_option("sqlalchemy.url", resolve_database_url())


def _configure(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=Base.metadata,
        render_as_batch=connection.dialect.name == "sqlite",
        compare_type=True,
        compare_server_default=True,
    )


async def _run() -> None:
    engine = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with engine.connect() as connection:
        await connection.run_sync(_configure)
        with context.begin_transaction():
            await connection.run_sync(lambda _: context.run_migrations())
    await engine.dispose()


asyncio.run(_run())
