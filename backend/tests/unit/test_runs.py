"""Tests for chat turns that run apart from the request that started them."""

import asyncio
import time
from collections.abc import AsyncIterator
from uuid import UUID, uuid4

import pytest
from pydantic_ai.ui.vercel_ai.response_types import BaseChunk, TextDeltaChunk

from hivegent import auth
from hivegent.db.conversations import OwnedConversation, models
from hivegent.runs import Prepared, Run, Runs
from hivegent.store import User

_A = TextDeltaChunk(id="t", delta="a")
_B = TextDeltaChunk(id="t", delta="b")


def _conversation(user: str = "alice", id: UUID | None = None) -> OwnedConversation:
    return models._conversation(user, id or uuid4())


async def _prepared(stream: AsyncIterator[BaseChunk]) -> Prepared:
    return Prepared(stream, [])


async def test_a_turn_outlives_its_follower_and_replays_to_the_next() -> None:
    """Leaving stops only the follower, and a reconnect reads the turn from its start."""
    release = asyncio.Event()

    async def turn() -> AsyncIterator[BaseChunk]:
        yield _A
        _ = await release.wait()
        yield _B

    runs = Runs()
    conversation = _conversation()
    run = runs.start(conversation, User("alice"), None, _prepared(turn()))
    first = run.follow(User("alice"))

    assert await anext(first) == _A
    await first.aclose()
    assert runs.get(conversation) is run
    release.set()

    assert [chunk async for chunk in run.follow(User("alice"))] == [_A, _B]
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
    run = runs.start(conversation, User("alice"), None, _prepared(turn()))
    assert await anext(run.follow(User("alice"))) == _A

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
        return Prepared(turn(), [])

    runs = Runs()
    conversation = _conversation()
    run = runs.start(conversation, User("alice"), None, prepare())
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
    run = runs.start(conversation, User("alice"), None, prepare())

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
    run = runs.start(owner, User("alice"), None, _prepared(turn()))

    assert runs.get(intruder) is None
    await runs.cancel(intruder)
    await runs.cancel_all(User(intruder.owner))
    assert not run.done

    release.set()
    await run.wait()


async def test_a_failing_turn_ends_on_an_error_chunk() -> None:
    async def turn() -> AsyncIterator[BaseChunk]:
        yield _A
        raise RuntimeError("boom")

    run = Runs().start(_conversation(), User("alice"), None, _prepared(turn()))
    await run.wait()

    assert run.chunks[0] == _A
    assert run.error is not None
    assert "boom" not in run.error


async def test_a_turn_stops_once_no_request_renews_its_authorization(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A fresh token of the same grants renews it, while a narrower one leaves it to lapse."""
    monkeypatch.setattr(auth, "_RENEWAL_SECONDS", 0)
    eng = frozenset({"eng"})

    async def turn() -> AsyncIterator[BaseChunk]:
        yield _A
        _ = await asyncio.Event().wait()

    def start() -> Run:
        principal = User("alice", read_groups=eng, expires_at=time.time() + 0.05)
        return Runs().start(_conversation(), principal, None, _prepared(turn()))

    lapsed = start()
    assert await anext(lapsed.follow(User("alice", read_groups=eng))) == _A
    auth.leases.renew(User("alice", expires_at=time.time() + 3600))
    await asyncio.wait_for(lapsed.wait(), 5)

    renewed = start()
    assert await anext(renewed.follow(User("alice", read_groups=eng))) == _A
    auth.leases.renew(User("alice", read_groups=eng, expires_at=time.time() + 3600))
    await asyncio.sleep(0.1)
    assert not renewed.done

    _ = renewed.task.cancel()
    await asyncio.wait_for(renewed.wait(), 5)
    assert lapsed.chunks == renewed.chunks == [_A]


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
        slot_free.append(runs.get(conversation) is None)
        after.set()

    async def prepare() -> Prepared:
        return Prepared(turn(), [], after=title)

    runs = Runs()
    conversation = _conversation()
    run = runs.start(conversation, User("alice"), None, prepare())
    assert await anext(run.follow(User("alice"))) == _A

    if stopped:
        await runs.cancel(conversation)
    else:
        release.set()
        await run.wait()
        _ = await after.wait()

    await runs.aclose()

    assert slot_free == ([] if stopped else [True])
