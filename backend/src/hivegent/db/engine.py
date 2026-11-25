"""Async engine and session factory.

One PostgreSQL database per deployment, addressed by
``settings.db.url``.  The engine and session-maker are built lazily on
first use so modules that import this one do not need a configured
URL until they actually connect, which keeps tests, doc builds, and tooling
imports cheap.
"""

import asyncio
import logging
import signal
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager, suppress
from contextvars import ContextVar
from functools import cache

from sqlalchemy import event, func, select
from sqlalchemy.engine.interfaces import DBAPIConnection
from sqlalchemy.ext.asyncio import (
    AsyncConnection,
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import ConnectionPoolEntry, NullPool

from ..config import settings

__all__ = [
    "ServerRunning",
    "engine_lifespan",
    "get_engine",
    "no_server_running",
    "resolve_database_url",
    "serving",
    "session",
    "transaction",
]

logger = logging.getLogger(__name__)

_ambient: ContextVar[AsyncSession | None] = ContextVar("ambient", default=None)


def resolve_database_url() -> str:
    """Return the configured SQLAlchemy URL.

    The URL is required at the point of first connect, there is no
    fallback dialect because PostgreSQL with ``pgvector`` is the only
    supported backend.
    """
    if settings.db.url is None:
        raise RuntimeError(
            "settings.db.url is unset.  Set HIVEGENT_DB__URL to a "
            "PostgreSQL async URL (e.g. postgresql+psycopg://...)."
        )

    return settings.db.url.get_secret_value()


@cache
def get_engine() -> AsyncEngine:
    """Build (or return the cached) async PostgreSQL engine.

    ``pool_pre_ping`` costs one round trip per checkout and earns it: the
    ``services-flake`` PostgreSQL is restarted freely underneath a running dev
    server, and without it the pool hands out connections the server has
    already closed, which surface as sporadic 500s rather than a reconnect.

    Every connection scans HNSW iteratively (pgvector 0.8), since a search is
    filtered to the caller's documents: a plain scan filters only the
    ``hnsw.ef_search`` nearest chunks and returns fewer than asked for once
    the caller sees a small share of them.  ``strict_order`` keeps the
    distance order the rank fusion reads.  It is set once connected rather
    than as a startup option, which a pooler such as PgBouncer refuses and
    which would replace the ``options`` of the configured URL.
    """
    engine = create_async_engine(
        resolve_database_url(),
        echo=settings.db.echo,
        pool_pre_ping=True,
        pool_size=settings.db.pool_size,
        max_overflow=settings.db.max_overflow,
        pool_timeout=settings.db.pool_timeout_seconds,
    )
    event.listen(engine.sync_engine, "connect", _scan_iteratively)

    return engine


def _scan_iteratively(
    dbapi_connection: DBAPIConnection, _record: ConnectionPoolEntry
) -> None:
    """Make *dbapi_connection* scan HNSW iteratively, committed so a rollback keeps it."""
    cursor = dbapi_connection.cursor()
    cursor.execute("SET hnsw.iterative_scan = strict_order")
    cursor.close()
    dbapi_connection.commit()


@cache
def _get_lock_engine() -> AsyncEngine:
    """Build (or return the cached) engine of the connections that hold session-level locks.

    Unpooled, so a lock held for the life of the process never takes a slot
    of :func:`get_engine`'s pool, and a released one closes its connection.
    """
    return create_async_engine(
        resolve_database_url(), echo=settings.db.echo, poolclass=NullPool
    )


@cache
def _get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    """Return the cached :class:`async_sessionmaker` bound to :func:`get_engine`."""
    return async_sessionmaker(get_engine(), expire_on_commit=False)


@asynccontextmanager
async def engine_lifespan() -> AsyncGenerator[None]:
    """Dispose the engines and clear the caches on shutdown."""
    try:
        yield
    finally:
        await get_engine().dispose()
        await _get_lock_engine().dispose()
        get_engine.cache_clear()
        _get_lock_engine.cache_clear()
        _get_sessionmaker.cache_clear()


@asynccontextmanager
async def session(joined: AsyncSession | None = None) -> AsyncGenerator[AsyncSession]:
    """Yield an :class:`AsyncSession` that commits on success, rolls back on error.

    A caller passing *joined*, or running inside :func:`transaction`, runs in
    that session's transaction instead, which its owner commits or rolls back,
    so several writes land together.
    """
    if (joined := joined or _ambient.get()) is not None:
        yield joined

        return

    async with _get_sessionmaker()() as s:
        try:
            yield s
            await s.commit()
        except Exception:
            await s.rollback()
            raise


@asynccontextmanager
async def transaction() -> AsyncGenerator[None]:
    """Run every :func:`session` opened inside in one transaction, committed on success."""
    async with session() as s:
        token = _ambient.set(s)

        try:
            yield
        finally:
            _ambient.reset(token)


_SERVER = func.hashtextextended("hivegent.server", 0)
"""The advisory lock the one running server or a host operation holds exclusively."""

_WATCH_SECONDS = 30.0
"""How often a server checks that the connection holding its lock is alive."""


class ServerRunning(RuntimeError):
    """A server or host operation found another server or host operation using the database."""


@asynccontextmanager
async def _lock_connection() -> AsyncGenerator[AsyncConnection]:
    """A connection of its own that holds a session-level lock and no transaction."""
    async with _get_lock_engine().connect() as conn:
        yield await conn.execution_options(isolation_level="AUTOCOMMIT")


@asynccontextmanager
async def _alone() -> AsyncGenerator[AsyncConnection]:
    """Hold the database alone for the block, on the connection that holds its lock.

    Raises:
        ServerRunning: When a server or a host operation holds it.
    """
    async with _lock_connection() as conn:
        if not await conn.scalar(select(func.pg_try_advisory_lock(_SERVER))):
            raise ServerRunning(
                "Another server or a host operation is running on this database, stop it first."
            )

        try:
            yield conn
        finally:
            await conn.execute(select(func.pg_advisory_unlock(_SERVER)))


@asynccontextmanager
async def serving() -> AsyncGenerator[None]:
    """Mark the server as running for as long as it serves.

    One server runs on a database and its data directory at a time, since the
    boot clears and recovers what only a dead server leaves behind, such as
    the spool of uploads and interrupted changes, so a second server or a host
    operation is refused.  The lock goes with the connection, so a server that dies
    leaves no stale lock behind, in a container as on a host, and a server
    whose connection dropped stops (:func:`_watch`).
    """
    async with _alone() as conn:
        watch = asyncio.create_task(_watch(conn))

        try:
            yield
        finally:
            watch.cancel()

            with suppress(asyncio.CancelledError):
                await watch


async def _watch(conn: AsyncConnection) -> None:
    """Stop the server once the connection holding its lock is gone, since the lock went with it.

    Another server or a host operation could then start beside it, so it shuts down rather than serve on.
    """
    while True:
        await asyncio.sleep(_WATCH_SECONDS)

        try:
            _ = await conn.scalar(select(1))
        except Exception:
            logger.critical(
                "Lost the database connection that holds the server's lock, shutting down",
                exc_info=True,
            )
            signal.raise_signal(signal.SIGTERM)
            return


@asynccontextmanager
async def no_server_running() -> AsyncGenerator[None]:
    """Hold off every server for a host operation that must run alone.

    Raises:
        ServerRunning: When a server holds :func:`serving`.
    """
    async with _alone():
        yield
