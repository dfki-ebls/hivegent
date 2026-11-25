"""Programmatic Alembic driver for runtime schema upgrades and checks.

Exposes :func:`apply_migrations`, called from the FastAPI lifespan and
the ``hivegent migrate`` CLI, and :func:`verify_schema`, which the lifespan
calls next so a database whose schema drifted from the models never serves.
Builds an Alembic ``Config`` in code so no external ``alembic.ini`` is
required at runtime, and the same ``env.py`` shipped inside the package
handles both CLI revisions and runtime upgrades, and
``resolve_database_url`` keeps a single source of truth for the target
database.
"""

import asyncio
import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any, Final, TypedDict

import sqlalchemy as sa
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext

from .engine import get_engine, resolve_database_url
from .models import Base, Chunk

__all__ = [
    "COMPARE",
    "apply_migrations",
    "build_alembic_config",
    "verify_schema",
]

logger = logging.getLogger(__name__)

# The Alembic env.py lives next to this module inside the installed
# wheel (``hivegent/migrations/env.py``).  Resolving the path through
# ``__file__`` keeps a single filesystem location whether the package
# is installed via uv editable or as a regular wheel; importlib's
# ``resources.files`` returns a ``MultiplexedPath`` for namespace
# packages, which Alembic does not accept.
_MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / "migrations"


def build_alembic_config() -> Config:
    """Return an Alembic :class:`Config` pointing at the packaged migrations."""
    cfg = Config()
    cfg.set_main_option("script_location", str(_MIGRATIONS_DIR))
    cfg.set_main_option("sqlalchemy.url", resolve_database_url())
    return cfg


def _upgrade_sync(revision: str) -> None:
    command.upgrade(build_alembic_config(), revision)


async def apply_migrations(revision: str = "head") -> None:
    """Upgrade the database to *revision* (default: ``head``).

    Runs in a worker thread because Alembic's command API is synchronous
    and the FastAPI lifespan executes inside the event loop.
    """
    logger.info("Applying database migrations to %s", revision)
    await asyncio.to_thread(_upgrade_sync, revision)


_RECONCILED = frozenset({Chunk.embedding.key, Chunk.tsv.key})


def _include_object(
    item: sa.schema.SchemaItem,
    name: str | None,
    type_: str,
    reflected: bool,
    compare_to: sa.schema.SchemaItem | None,
) -> bool:
    """Leave out the columns of ``chunks`` that cbrkit's ``Pgvector.reconcile`` fits at boot, and their indexes.

    Their dimension, generated expression, and comment follow the settings, not a revision.
    """
    if isinstance(item, sa.Column):
        columns: list[sa.Column[Any]] = [item]
    elif isinstance(item, sa.Index):
        columns = list(item.columns)
    else:
        return True

    return not any(
        column.table.name == Chunk.__tablename__ and column.name in _RECONCILED
        for column in columns
    )


class _Compare(TypedDict):
    compare_type: bool
    compare_server_default: bool
    include_object: Callable[
        [sa.schema.SchemaItem, str | None, str, bool, sa.schema.SchemaItem | None],
        bool,
    ]


COMPARE: Final = _Compare(
    compare_type=True, compare_server_default=True, include_object=_include_object
)
"""How the models are compared with the database, by revisions and by :func:`verify_schema` alike."""


def _differences(conn: sa.Connection) -> list[Any]:
    context = MigrationContext.configure(conn, opts=dict(COMPARE))

    return list(compare_metadata(context, Base.metadata))


async def verify_schema() -> None:
    """Refuse to serve a database whose schema differs from the models.

    It compares as a new revision would, so a difference is a model change
    that lacks its migration.

    Raises:
        RuntimeError: Naming every difference.
    """
    async with get_engine().connect() as conn:
        differences = await conn.run_sync(_differences)

    if differences:
        raise RuntimeError(
            "The database schema differs from the models, generate a migration for: "
            + "; ".join(map(str, differences))
        )
