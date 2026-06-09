"""Async concurrency helpers."""

import asyncio
from collections.abc import Coroutine
from typing import Any

__all__ = ["shield_to_completion"]


async def shield_to_completion[T](coro: Coroutine[Any, Any, T]) -> T:
    """Run *coro* to completion, surviving cancellation of the calling task.

    ``asyncio.shield`` protects the wrapped coroutine from cancellation, but its
    awaiter still raises ``CancelledError`` immediately, leaving the work
    running *detached*. For a critical section that mutates shared state while
    holding a lock, that detachment is a bug: the lock releases before the work
    finishes, so whatever runs next races the orphaned task. This helper instead
    keeps re-awaiting the shielded task through the caller's own cancellation, so
    the work is done before control returns. A deferred cancellation is re-raised
    afterwards so it is never swallowed; an error from *coro* propagates as-is.

    Args:
        coro: The coroutine to run to completion.

    Returns:
        The coroutine's result.
    """
    task = asyncio.ensure_future(coro)
    cancelled: asyncio.CancelledError | None = None
    while True:
        try:
            result = await asyncio.shield(task)
        except asyncio.CancelledError as exc:
            if task.done():
                raise
            # Our own cancellation while the protected work is still in flight:
            # remember it and keep waiting for the task to finish.
            cancelled = exc
        else:
            if cancelled is not None:
                raise cancelled
            return result
