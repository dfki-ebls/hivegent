"""Tests for chat turns that run apart from the request that started them."""

import asyncio
from collections.abc import AsyncIterator
from uuid import UUID, uuid4

from pydantic_ai.ui.vercel_ai.response_types import BaseChunk, TextDeltaChunk

from hivegent.db.conversations import OwnedConversation
from hivegent.runs import Prepared, Runs
from hivegent.store import User

_A = TextDeltaChunk(id="t", delta="a")
_B = TextDeltaChunk(id="t", delta="b")


def _conversation(user: str = "alice", id: UUID | None = None) -> OwnedConversation:
    return OwnedConversation(user, id or uuid4())


async def _prepared(stream: AsyncIterator[BaseChunk]) -> Prepared:
    return stream, None


async def test_a_turn_outlives_its_follower_and_replays_to_the_next() -> None:
    """Leaving stops only the follower, and a reconnect reads the turn from its start."""
    release = asyncio.Event()

    async def turn() -> AsyncIterator[BaseChunk]:
        yield _A
        _ = await release.wait()
        yield _B

    runs = Runs()
    conversation = _conversation()
    run = runs.start(conversation, None, _prepared(turn()))
    first = run.follow()

    assert await anext(first) == _A
    await first.aclose()
    assert runs.get(conversation) is run
    release.set()

    assert [chunk async for chunk in run.follow()] == [_A, _B]
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
    conversation = _conversation()
    run = runs.start(conversation, None, _prepared(turn()))
    assert await anext(run.follow()) == _A

    await runs.cancel(conversation)

    assert stored == ["cut short"]
    assert run.done
    assert runs.get(conversation) is None


async def test_a_turn_stopped_while_preparing_never_streams() -> None:
    """A cancel during preparation keeps the turn from starting afterwards."""
    preparing = asyncio.Event()
    streamed: list[str] = []

    async def turn() -> AsyncIterator[BaseChunk]:
        streamed.append("started")
        yield _A

    async def prepare() -> Prepared:
        preparing.set()
        _ = await asyncio.Event().wait()
        return turn(), None

    runs = Runs()
    conversation = _conversation()
    run = runs.start(conversation, None, prepare())
    _ = await preparing.wait()

    await runs.cancel(conversation)

    assert not await run.prepared()
    assert streamed == []
    assert runs.get(conversation) is None


async def test_a_refused_turn_frees_its_slot_and_keeps_the_refusal() -> None:
    """A refusal in preparation leaves the slot and reaches whoever follows."""

    async def prepare() -> Prepared:
        raise ValueError("refused")

    runs = Runs()
    conversation = _conversation()
    run = runs.start(conversation, None, prepare())

    assert not await run.prepared()
    assert isinstance(run.refusal, ValueError)
    assert runs.get(conversation) is None


async def test_a_turn_is_hidden_from_other_users_of_its_id() -> None:
    """Another user addressing the same id finds no turn to follow or stop."""
    release = asyncio.Event()

    async def turn() -> AsyncIterator[BaseChunk]:
        _ = await release.wait()
        yield _A

    runs = Runs()
    owner = _conversation()
    intruder = _conversation("mallory", owner.id)
    run = runs.start(owner, None, _prepared(turn()))

    assert runs.get(intruder) is None
    await runs.cancel(intruder)
    await runs.cancel_all(User(intruder.owner))
    assert not run.done

    release.set()
    await run.wait()
