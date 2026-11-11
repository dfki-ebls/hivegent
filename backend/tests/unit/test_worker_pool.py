"""Tests for the persistent pipeline worker pool's recovery logic.

Stateless: no real worker processes are spawned.  The executor is
monkeypatched, so these run without touching multiprocessing (real
cross-process execution is a manual smoke test, like the other live-surface
paths).
"""

import asyncio
from collections.abc import Callable
from concurrent.futures.process import BrokenProcessPool

import pytest

from hivegent.workers.pool import ProcessPool


async def test_pool_rebuilds_and_retries_once_on_dead_worker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A dead worker poisons the executor; run() discards it and retries once."""
    pool = ProcessPool()
    first, second = object(), object()
    handles = iter([first, second])
    discarded: list[object] = []

    async def fake_current(self: ProcessPool) -> object:
        return next(handles)

    async def fake_discard(self: ProcessPool, broken: object) -> None:
        discarded.append(broken)

    async def fake_run_in_executor(
        executor: object, func: Callable[..., object], *args: object
    ) -> object:
        if executor is first:
            raise BrokenProcessPool("worker died")
        return func(*args)

    # ProcessPool is slotted, so patch the methods on the class, not the instance.
    monkeypatch.setattr(ProcessPool, "_current", fake_current)
    monkeypatch.setattr(ProcessPool, "_discard", fake_discard)
    monkeypatch.setattr(
        asyncio.get_running_loop(), "run_in_executor", fake_run_in_executor
    )

    result = await pool.run(lambda x: x + 1, 41)

    assert result == 42
    # The broken executor was dropped exactly once before the successful retry.
    assert discarded == [first]
