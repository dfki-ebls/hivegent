"""Tests for chat turns that run apart from the request that started them."""

import asyncio
from collections.abc import AsyncIterator

import pytest
from pydantic_ai.ui.vercel_ai.response_types import BaseChunk, TextDeltaChunk

from hivegent.db.conversations import models
from hivegent.runs import Prepared, Runs
from hivegent.store import User
from tests.helpers import owned

_A = TextDeltaChunk(id="t", delta="a")
_B = TextDeltaChunk(id="t", delta="b")
_ALICE = User("alice")
_CONVERSATION = owned("c1", _ALICE)


async def _prepared(stream: AsyncIterator[BaseChunk]) -> Prepared:
    return Prepared(stream, [])


async def _never() -> AsyncIterator[BaseChunk]:
    yield _A
    _ = await asyncio.Event().wait()


async def test_a_turn_outlives_its_follower_and_replays_to_the_next() -> None:
    """Leaving stops only the follower, and a reconnect reads the turn from its start."""
    release = asyncio.Event()

    async def turn() -> AsyncIterator[BaseChunk]:
        yield _A
        _ = await release.wait()
        yield _B

    runs = Runs()
    run = runs.start(_CONVERSATION, _ALICE, None, _prepared(turn()))
    first = run.follow(_ALICE)

    assert await anext(first) == _A
    await first.aclose()
    assert runs.get(_CONVERSATION) is run
    release.set()

    assert [chunk async for chunk in run.follow(_ALICE)] == [_A, _B]
    assert runs.get(_CONVERSATION) is None


async def test_a_stopped_turn_stores_what_it_got_to_before_the_stop_returns() -> None:
    """A cancel reaches the turn itself, whose cleanup is where a cut-short turn is stored."""
    stored: list[str] = []

    async def turn() -> AsyncIterator[BaseChunk]:
        try:
            async for chunk in _never():
                yield chunk
        finally:
            stored.append("cut short")

    runs = Runs()
    run = runs.start(_CONVERSATION, _ALICE, None, _prepared(turn()))
    assert await anext(run.follow(_ALICE)) == _A

    await runs.cancel(_CONVERSATION)

    assert stored == ["cut short"]
    assert run.done
    assert runs.get(_CONVERSATION) is None


async def test_a_turn_stopped_while_preparing_never_streams() -> None:
    """A cancel during preparation keeps the turn from starting afterwards."""
    preparing = asyncio.Event()

    async def prepare() -> Prepared:
        preparing.set()
        _ = await asyncio.Event().wait()
        raise AssertionError("prepared after the stop")

    runs = Runs()
    run = runs.start(_CONVERSATION, _ALICE, None, prepare())
    _ = await preparing.wait()

    await runs.cancel(_CONVERSATION)

    assert not await run.prepared()
    assert run.chunks == []
    assert runs.get(_CONVERSATION) is None


async def test_a_refused_turn_frees_its_slot_and_keeps_the_refusal() -> None:
    async def prepare() -> Prepared:
        raise ValueError("refused")

    runs = Runs()
    run = runs.start(_CONVERSATION, _ALICE, None, prepare())

    assert not await run.prepared()
    assert isinstance(run.refusal, ValueError)
    assert runs.get(_CONVERSATION) is None


async def test_a_turn_is_hidden_from_other_users_of_its_id() -> None:
    """Another user addressing the same id finds no turn to follow or stop."""
    runs = Runs()
    run = runs.start(_CONVERSATION, _ALICE, None, _prepared(_never()))
    assert await anext(run.follow(_ALICE)) == _A
    intruder = models._conversation("mallory", _CONVERSATION.id)

    assert runs.get(intruder) is None
    await runs.cancel(intruder)
    await runs.cancel_all(User("mallory"))
    assert not run.done

    await runs.aclose()


async def test_a_failing_turn_ends_on_an_error_chunk() -> None:
    async def turn() -> AsyncIterator[BaseChunk]:
        yield _A
        raise RuntimeError("boom")

    run = Runs().start(_CONVERSATION, _ALICE, None, _prepared(turn()))
    await run.wait()

    assert run.chunks[0] == _A
    assert run.error is not None
    assert "boom" not in run.error


@pytest.mark.parametrize("stopped", [False, True], ids=["ended", "stopped"])
async def test_the_work_after_a_turn_runs_once_it_ended_unstopped(
    stopped: bool,
) -> None:
    """The work after a turn holds neither its slot nor its followers, and a stop skips it."""
    release = asyncio.Event()
    after = asyncio.Event()
    slot_free: list[bool] = []

    async def turn() -> AsyncIterator[BaseChunk]:
        yield _A
        _ = await release.wait()

    async def title() -> None:
        slot_free.append(runs.get(_CONVERSATION) is None)
        after.set()

    async def prepare() -> Prepared:
        return Prepared(turn(), [], after=title)

    runs = Runs()
    run = runs.start(_CONVERSATION, _ALICE, None, prepare())
    assert await anext(run.follow(_ALICE)) == _A

    if stopped:
        await runs.cancel(_CONVERSATION)
    else:
        release.set()
        await run.wait()
        _ = await after.wait()

    await runs.aclose()

    assert slot_free == ([] if stopped else [True])
