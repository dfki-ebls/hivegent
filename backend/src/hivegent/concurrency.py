"""Async concurrency helpers."""

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable, Coroutine, Iterable
from typing import Any

__all__ = ["bounded_as_completed", "shield_to_completion"]


async def bounded_as_completed[T, R](
    items: Iterable[T],
    run: Callable[[T], Awaitable[R]],
    *,
    limit: int,
) -> AsyncIterator[R]:
    """Yield ``run(item)`` results as they finish, at most *limit* in flight.

    A semaphore bounds concurrency so a large batch never spawns unbounded work
    or holds more than *limit* inputs resident at once; results stream in
    completion order, not input order.  On cancellation (or an early break by the
    consumer) every unfinished task is cancelled and awaited to completion, so a
    task that owns a rollback — e.g. a phased upload unwinding a half-written
    entry — finishes unwinding before this generator returns, never detached.

    *run* is expected to convert its own failures into a returned value rather
    than raise, so one bad item does not abort the batch.

    Args:
        items: The inputs to process.
        run: Coroutine function applied to each input.
        limit: Maximum number of *run* invocations in flight at once.

    Yields:
        Each ``run(item)`` result, in the order the calls complete.
    """
    semaphore = asyncio.Semaphore(limit)

    async def _guarded(item: T) -> R:
        async with semaphore:
            return await run(item)

    tasks = [asyncio.ensure_future(_guarded(item)) for item in items]
    try:
        for future in asyncio.as_completed(tasks):
            yield await future
    finally:
        for task in tasks:
            task.cancel()

        async def _drain() -> None:
            await asyncio.gather(*tasks, return_exceptions=True)

        await shield_to_completion(_drain())


async def shield_to_completion[T](coro: Coroutine[Any, Any, T]) -> T:
    """Run *coro* to completion, surviving cancellation of the calling task.

    ``asyncio.shield`` protects the wrapped coroutine from cancellation, but its
    awaiter still raises ``CancelledError`` immediately, leaving the work
    running *detached*. For a critical section that mutates shared state while
    holding a lock, that detachment is a bug: the lock releases before the work
    finishes, so whatever runs next races the orphaned task. This helper instead
    keeps re-awaiting the shielded task through the caller's own cancellation, so
    the work is done before control returns. A deferred cancellation is re-raised
    afterwards so it is never swallowed — even when the work itself ends by
    raising, the caller's cancellation still takes precedence over that error.

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
        except BaseException:
            # The protected work finished by raising.  A deferred cancellation of
            # our own takes precedence (the caller asked to stop); otherwise the
            # work's own error propagates unchanged.
            if cancelled is not None:
                raise cancelled
            raise
        else:
            if cancelled is not None:
                raise cancelled
            return result
