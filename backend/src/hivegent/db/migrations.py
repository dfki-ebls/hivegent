"""Programmatic Alembic driver for runtime schema upgrades.

Exposes :func:`apply_migrations`, called from the FastAPI lifespan and
the ``hivegent migrate`` CLI.  Builds an Alembic ``Config`` in code so
no external ``alembic.ini`` is required at runtime — the same
``env.py`` shipped inside the package handles both CLI revisions and
runtime upgrades, and ``resolve_database_url`` keeps a single source
of truth for the target database.
"""

import asyncio
import logging
from pathlib import Path

from alembic import command
from alembic.config import Config

from .engine import resolve_database_url

__all__ = ["apply_migrations", "build_alembic_config"]

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
