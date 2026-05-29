"""Async engine and session factory.

One PostgreSQL database per deployment, addressed by
``settings.db.url``.  The engine and session-maker are built lazily on
first use so modules that import this one do not need a configured
URL until they actually connect — keeps tests, doc builds, and tooling
imports cheap.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from functools import cache

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from ..config import settings

__all__ = ["get_engine", "get_sessionmaker", "resolve_database_url", "session"]


def resolve_database_url() -> str:
    """Return the configured SQLAlchemy URL.

    The URL is required at the point of first connect; there is no
    fallback dialect because PostgreSQL with ``pgvector`` is the only
    supported backend.
    """
    if not settings.db.url:
        raise RuntimeError(
            "settings.db.url is unset.  Set HIVEGENT_DB__URL to a "
            "PostgreSQL async URL (e.g. postgresql+psycopg://...)."
        )
    return settings.db.url


@cache
def get_engine() -> AsyncEngine:
    """Build (or return the cached) async PostgreSQL engine."""
    return create_async_engine(
        resolve_database_url(), echo=settings.db.echo, future=True
    )


@cache
def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    """Return the cached :class:`async_sessionmaker` bound to :func:`get_engine`."""
    return async_sessionmaker(get_engine(), expire_on_commit=False)


@asynccontextmanager
async def session() -> AsyncIterator[AsyncSession]:
    """Yield an :class:`AsyncSession` that commits on success, rolls back on error."""
    async with get_sessionmaker()() as s:
        try:
            yield s
            await s.commit()
        except Exception:
            await s.rollback()
            raise
