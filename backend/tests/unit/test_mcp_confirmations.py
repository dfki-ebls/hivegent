"""Each MCP confirmation runs its call once, across replicas, by its nonce in PostgreSQL."""

import time
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest
from sqlalchemy.dialects import postgresql
from sqlalchemy.sql import Delete, Insert

from hivegent.db import confirmations


async def test_a_nonce_is_spent_once(monkeypatch: pytest.MonkeyPatch) -> None:
    spent: set[str] = set()
    swept: list[str] = []

    class Session:
        async def execute(self, statement: Insert | Delete) -> SimpleNamespace:
            sql = str(statement.compile(dialect=postgresql.dialect()))

            if isinstance(statement, Delete):
                swept.append(sql)

                return SimpleNamespace()

            assert "ON CONFLICT DO NOTHING" in sql
            nonce = statement.compile().params["nonce"]
            fresh = nonce not in spent
            spent.add(nonce)

            return SimpleNamespace(scalar_one_or_none=lambda: nonce if fresh else None)

    @asynccontextmanager
    async def session() -> AsyncGenerator[Session]:
        yield Session()

    monkeypatch.setattr(confirmations, "session", session)
    nonces = confirmations.PostgresNonces()
    expiry = int(time.time()) + 60

    assert await nonces.spend("a", expiry) is True
    assert await nonces.spend("a", expiry) is False
    assert await nonces.spend("b", expiry) is True
    assert len(swept) == 3 and "expires_at <= now()" in swept[0]
