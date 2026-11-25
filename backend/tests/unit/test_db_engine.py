"""The engines split pooled queries from the connections that hold locks."""

from collections.abc import Iterator

import pytest
from pydantic import SecretStr
from sqlalchemy.pool import NullPool, QueuePool

from hivegent.config import settings
from hivegent.config.infra import DatabaseSettings
from hivegent.db import engine


@pytest.fixture(autouse=True)
def _engines(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Build the engines from a URL nothing connects to, and forget them after."""
    monkeypatch.setattr(
        settings,
        "db",
        DatabaseSettings(
            url=SecretStr("postgresql+psycopg://hivegent@localhost/hivegent"),
            pool_size=3,
            max_overflow=2,
            pool_timeout_seconds=4.0,
        ),
    )
    yield
    engine.get_engine.cache_clear()
    engine._get_lock_engine.cache_clear()


def test_locks_take_no_slot_of_the_configured_pool() -> None:
    pool = engine.get_engine().pool

    assert isinstance(pool, QueuePool)
    assert (pool.size(), pool._max_overflow, pool.timeout()) == (3, 2, 4.0)
    assert isinstance(engine._get_lock_engine().pool, NullPool)
