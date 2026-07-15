"""Tests for the persistent pipeline worker pool's dispatch and recovery logic.

Stateless: no real worker processes are spawned.  The pool-active path is
exercised with the executor call monkeypatched, so these run without touching
multiprocessing (real cross-process execution is a manual smoke test, like the
other live-surface paths).
"""

import asyncio
from collections.abc import Callable
from concurrent.futures.process import BrokenProcessPool

import pytest

from hivegent.config import settings
from hivegent.workers.pool import ProcessPool, run_offloaded


async def test_run_offloaded_runs_in_process_when_pool_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With the pool off, work runs in-process — proven by a non-picklable closure."""
    monkeypatch.setattr(settings.compute, "worker_processes", 1)
    captured: list[int] = []

    def work(x: int) -> int:
        # A local closure cannot be pickled, so reaching this at all proves the
        # call ran in a thread here, not shipped to a worker process.
        captured.append(x)
        return x * 2

    result = await run_offloaded(work, 21, fallback_lock=asyncio.Lock())

    assert result == 42
    assert captured == [21]


async def test_run_offloaded_dispatches_to_pool_when_active(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With the pool on, work is handed to the process pool rather than a thread."""
    monkeypatch.setattr(settings.compute, "worker_processes", 2)
    calls: list[tuple[object, tuple[object, ...]]] = []

    async def fake_run(self: ProcessPool, func: object, /, *args: object) -> str:
        calls.append((func, args))
        return "from-pool"

    # ProcessPool is slotted, so patch the method on the class, not the instance.
    monkeypatch.setattr(ProcessPool, "run", fake_run)

    def work(x: int) -> int:
        return x

    result = await run_offloaded(work, 7, fallback_lock=asyncio.Lock())

    assert result == "from-pool"
    assert calls == [(work, (7,))]


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
