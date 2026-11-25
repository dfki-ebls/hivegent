"""Tests for the ownership of lifespan resources."""

import asyncio
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from hivegent.lifespan import LifespanResource


async def test_lifespan_reserves_ownership_before_resource_startup() -> None:
    """Concurrent startup opens one resource and rejects the second owner."""
    opened: list[bool] = []

    @asynccontextmanager
    async def open_resource() -> AsyncGenerator[object]:
        opened.append(True)
        await asyncio.sleep(0)
        yield object()

    holder = LifespanResource("Test resource", "test_lifespan", open_resource)

    async def enter() -> None:
        async with holder.lifespan():
            await asyncio.sleep(0)

    results = await asyncio.gather(enter(), enter(), return_exceptions=True)

    assert opened == [True]
    assert sum(isinstance(result, RuntimeError) for result in results) == 1
