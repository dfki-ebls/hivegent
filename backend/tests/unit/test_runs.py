"""Tests for chat turns that run apart from the request that started them."""

import asyncio
from collections.abc import AsyncIterator
from uuid import uuid4

from pydantic_ai.ui.vercel_ai.response_types import BaseChunk, TextDeltaChunk

from hivegent.server.runs import Runs

_A = TextDeltaChunk(id="t", delta="a")
_B = TextDeltaChunk(id="t", delta="b")


async def _prepared(stream: AsyncIterator[BaseChunk]) -> AsyncIterator[BaseChunk]:
    return stream


async def test_a_turn_outlives_its_follower_and_replays_to_the_next() -> None:
    """Leaving stops only the follower, and a reconnect reads the turn from its start."""
    release = asyncio.Event()

    async def turn() -> AsyncIterator[BaseChunk]:
        yield _A
        _ = await release.wait()
        yield _B

    runs = Runs()
    conversation = uuid4()
    run = await runs.start(conversation, None, _prepared(turn()))
    first = run.follow()

    assert await anext(first) == _A
    await first.aclose()
    assert runs.get(conversation) is run
    release.set()

    assert [chunk async for chunk in run.follow()] == [_A, _B]
    await asyncio.sleep(0)
    assert runs.get(conversation) is None


async def test_a_stopped_turn_stores_what_it_got_to_before_the_stop_returns() -> None:
    """A cancel reaches the turn itself, whose cleanup is where a cut-short turn is stored."""
    stored: list[str] = []

    async def turn() -> AsyncIterator[BaseChunk]:
        try:
            yield _A
            _ = await asyncio.Event().wait()
        finally:
            stored.append("cut short")

    runs = Runs()
    conversation = uuid4()
    run = await runs.start(conversation, None, _prepared(turn()))
    assert await anext(run.follow()) == _A

    await runs.cancel(conversation)

    assert stored == ["cut short"]
    assert run.done
