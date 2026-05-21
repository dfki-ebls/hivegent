"""Async engine and session factory.

One database per deployment, addressed by ``settings.db.url``.  Defaults
to SQLite under ``data_dir`` so a fresh checkout runs with no setup.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy import event
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from ..config import settings
from .models import Base

__all__ = ["Session", "engine", "init_database", "session"]


def _resolve_url() -> str:
    """Build the SQLAlchemy URL, defaulting to SQLite under ``data_dir``."""
    if settings.db.url:
        return settings.db.url
    db_path = settings.data_dir / "hivegent.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    return f"sqlite+aiosqlite:///{db_path}"


def _build_engine() -> AsyncEngine:
    """Build the async engine with SQLite PRAGMAs attached if applicable."""
    eng = create_async_engine(_resolve_url(), echo=settings.db.echo, future=True)
    if eng.dialect.name == "sqlite":
        _attach_sqlite_pragmas(eng)
    return eng


def _attach_sqlite_pragmas(eng: AsyncEngine) -> None:
    """Enable foreign keys, WAL, and a sane busy timeout on every connection."""

    @event.listens_for(eng.sync_engine, "connect")
    def _on_connect(dbapi_conn, _record):  # type: ignore[no-untyped-def]
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA synchronous=NORMAL")
        cur.execute("PRAGMA busy_timeout=5000")
        cur.close()


engine: AsyncEngine = _build_engine()
Session = async_sessionmaker(engine, expire_on_commit=False)


@asynccontextmanager
async def session() -> AsyncIterator[AsyncSession]:
    """Yield an :class:`AsyncSession` that commits on success, rolls back on error."""
    async with Session() as s:
        try:
            yield s
            await s.commit()
        except Exception:
            await s.rollback()
            raise


async def init_database() -> None:
    """Create tables from the model metadata.

    Suitable for development and tests.  Production schema changes should
    go through Alembic migrations.
    """
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
