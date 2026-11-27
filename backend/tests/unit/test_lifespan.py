"""Tests for the ownership of lifespan resources."""

import asyncio
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

import pytest

from hivegent.lifespan import LifespanResource


async def test_lifespan_reserves_ownership_before_resource_startup() -> None:
    """A second owner is rejected while the first is still opening its resource, which opens once."""
    opening = asyncio.Event()
    release = asyncio.Event()
    opened: list[bool] = []

    @asynccontextmanager
    async def open_resource() -> AsyncGenerator[object]:
        opened.append(True)
        opening.set()
        await release.wait()
        yield object()

    holder = LifespanResource("Test resource", "test_lifespan", open_resource)

    async def enter() -> None:
        async with holder.lifespan():
            pass

    first = asyncio.create_task(enter())
    await opening.wait()

    with pytest.raises(RuntimeError):
        await enter()

    release.set()
    await first

    assert opened == [True]
