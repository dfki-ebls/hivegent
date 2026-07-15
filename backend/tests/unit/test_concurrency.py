"""Tests for cancellation-safe async and process concurrency helpers."""

import asyncio
import os
import time
from multiprocessing import active_children

import pytest

from hivegent.concurrency import bounded_as_completed, shield_to_completion
from hivegent.workers.isolation import (
    WorkerCrashError,
    WorkerTimeoutError,
    run_isolated,
)


def _child_pids() -> set[int | None]:
    """Return the current multiprocessing child process identifiers."""
    return {process.pid for process in active_children()}


async def test_runs_to_completion_and_reraises_cancel() -> None:
    """A cancelled caller still lets the protected work finish, then propagates."""
    done: list[bool] = []

    async def work() -> str:
        await asyncio.sleep(0.05)
        done.append(True)
        return "ok"

    async def caller() -> str:
        return await shield_to_completion(work())

    task = asyncio.ensure_future(caller())
    await asyncio.sleep(0.01)
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task
    # The cancellation was honored, but only after the work ran to completion.
    assert done == [True]


async def test_returns_result_without_cancellation() -> None:
    assert await shield_to_completion(asyncio.sleep(0, result="value")) == "value"


async def test_propagates_work_error() -> None:
    async def boom() -> None:
        raise ValueError("nope")

    with pytest.raises(ValueError, match="nope"):
        await shield_to_completion(boom())


async def test_bounded_as_completed_yields_all_within_limit() -> None:
    """Every item is processed, never more than *limit* concurrently in flight."""
    in_flight = 0
    peak = 0

    async def run(item: int) -> int:
        nonlocal in_flight, peak
        in_flight += 1
        peak = max(peak, in_flight)
        await asyncio.sleep(0.01)
        in_flight -= 1
        return item * 2

    results = [r async for r in bounded_as_completed(range(10), run, limit=3)]

    assert sorted(results) == [i * 2 for i in range(10)]
    assert peak <= 3


async def test_bounded_as_completed_unwinds_in_flight_on_cancel() -> None:
    """A cancelled consumer cancels and awaits every unfinished task to completion."""
    started = 0
    rolled_back = 0

    async def run(item: int) -> int:
        nonlocal started, rolled_back
        started += 1
        try:
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            # Stand in for a phased upload unwinding its half-written entry.
            rolled_back += 1
            raise
        return item

    async def consume() -> None:
        async for _ in bounded_as_completed(range(5), run, limit=2):
            pass

    task = asyncio.ensure_future(consume())
    await asyncio.sleep(0.02)
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task

    # Only the two that got a slot ran, and each unwound before the loop returned.
    assert started == 2
    assert rolled_back == 2


async def test_isolated_worker_crash_does_not_abort_parent() -> None:
    before = _child_pids()

    with pytest.raises(WorkerCrashError, match="exited with code"):
        await run_isolated(os.abort)

    assert _child_pids() == before


async def test_isolated_worker_timeout_reaps_child() -> None:
    before = _child_pids()

    with pytest.raises(WorkerTimeoutError, match="timed out"):
        await run_isolated(time.sleep, 10, timeout_seconds=0.1)

    assert _child_pids() == before


async def test_isolated_worker_cancellation_reaps_child() -> None:
    before = _child_pids()
    task = asyncio.create_task(run_isolated(time.sleep, 10, timeout_seconds=None))
    await asyncio.sleep(0.1)
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task

    assert _child_pids() == before
