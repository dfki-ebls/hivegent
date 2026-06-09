"""`shield_to_completion` runs work to completion despite caller cancellation."""

import asyncio

import pytest

from hivegent.concurrency import shield_to_completion


async def test_runs_to_completion_and_reraises_cancel() -> None:
    """A cancelled caller still lets the protected work finish, then propagates."""
    done = False

    async def work() -> str:
        nonlocal done
        await asyncio.sleep(0.05)
        done = True
        return "ok"

    async def caller() -> str:
        return await shield_to_completion(work())

    task = asyncio.ensure_future(caller())
    await asyncio.sleep(0.01)
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task
    # The cancellation was honored, but only after the work ran to completion.
    assert done is True


async def test_returns_result_without_cancellation() -> None:
    assert await shield_to_completion(asyncio.sleep(0, result="value")) == "value"


async def test_propagates_work_error() -> None:
    async def boom() -> None:
        raise ValueError("nope")

    with pytest.raises(ValueError, match="nope"):
        await shield_to_completion(boom())
