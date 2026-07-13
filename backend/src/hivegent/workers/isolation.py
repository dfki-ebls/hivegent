"""Run crash-prone native code in a throwaway spawned worker process.

:func:`run_isolated` runs a call in a fresh single-use process so a native
crash (segfault/abort) kills only the worker and surfaces as
:class:`WorkerCrashError`, leaving the server up.

To adopt it for a new code path: put the crash-prone function in a
dependency-light leaf module (see :mod:`hivegent.workers`) and call
``await run_isolated(func, *args)``; map :class:`WorkerCrashError` to
whatever domain error the caller already handles.
"""

import asyncio
from collections.abc import Callable
from concurrent.futures import ProcessPoolExecutor
from concurrent.futures.process import BrokenProcessPool
from multiprocessing import get_context

__all__ = ["WorkerCrashError", "run_isolated"]


class WorkerCrashError(Exception):
    """A spawned isolation worker died on a native fault (segfault/abort)."""


async def run_isolated[T](func: Callable[..., T], *args: object) -> T:
    """Run *func* in a fresh spawned process and return its result.

    *func* must be importable by qualified name (a module-level function)
    and its arguments and return value picklable.  A normal exception
    raised by *func* propagates unchanged; a crash that kills the worker is
    remapped to :class:`WorkerCrashError`.  Spawn is forced over the
    default fork so the worker inherits none of the parent's event loop,
    threads, or heap.  The blocking pool lifecycle runs on a thread so the
    event loop stays free.
    """

    def blocking() -> T:
        with ProcessPoolExecutor(
            max_workers=1, mp_context=get_context("spawn")
        ) as pool:
            try:
                return pool.submit(func, *args).result()

            except BrokenProcessPool as exc:
                name = getattr(func, "__qualname__", func)
                raise WorkerCrashError(
                    f"isolation worker crashed running {name}"
                ) from exc

    return await asyncio.to_thread(blocking)
