"""Run crash-prone native code in a supervised, single-use process.

:func:`run_isolated` runs one call in a **fresh** spawned process so a native
crash (segfault/abort) or a runaway hang kills only the worker and surfaces as
:class:`WorkerCrashError` or :class:`WorkerTimeoutError`, leaving the server up.

This is the single-use, timeout-supervised sibling of the persistent
:mod:`hivegent.workers.pool`; see :mod:`hivegent.workers` for which to use.

To adopt it for a new code path: put the crash-prone function in a
dependency-light leaf module (see :mod:`hivegent.workers`) and call
``await run_isolated(func, *args)``; map the worker errors to whatever domain
error the caller already handles.
"""

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from multiprocessing import get_context
from multiprocessing.connection import _ConnectionBase  # pyright: ignore[reportPrivateUsage]
from multiprocessing.process import BaseProcess
from typing import cast

from ..concurrency import shield_to_completion
from ..config import settings

__all__ = ["WorkerCrashError", "WorkerTimeoutError", "run_isolated"]

_worker_slots = asyncio.Semaphore(settings.isolation.max_workers)


class WorkerCrashError(Exception):
    """A spawned isolation worker exited without returning a result."""


class WorkerTimeoutError(Exception):
    """A spawned isolation worker exceeded its execution time limit."""


@dataclass(slots=True, frozen=True)
class _Success[T]:
    """A value returned by an isolation worker."""

    value: T


@dataclass(slots=True, frozen=True)
class _Failure:
    """An exception raised normally by an isolation worker."""

    error: Exception


type _Outcome[T] = _Success[T] | _Failure
"""What a worker sends back: its return value or the exception it raised."""


def _worker_entry[T](
    connection: _ConnectionBase,
    func: Callable[..., T],
    args: tuple[object, ...],
) -> None:
    """Execute one call and send its outcome to the supervising process."""
    outcome: _Outcome[T]

    try:
        try:
            outcome = _Success(func(*args))
        except Exception as exc:
            outcome = _Failure(exc)

        connection.send(outcome)
    finally:
        connection.close()


def _spawn[T](
    func: Callable[..., T], args: tuple[object, ...], name: str
) -> tuple[BaseProcess, _ConnectionBase]:
    """Start a fresh spawned worker and return it with its result pipe.

    Spawn is forced over the default fork so the worker inherits none of the
    parent's event loop, threads, or heap.
    """
    context = get_context("spawn")
    receive, send = context.Pipe(duplex=False)
    process = context.Process(
        target=_worker_entry,
        args=(send, func, args),
        name=f"hivegent-{name}",
        daemon=True,
    )
    process.start()
    send.close()

    return process, receive


def _wait[T](
    process: BaseProcess,
    receive: _ConnectionBase,
    timeout_seconds: float | None,
    name: str,
) -> _Outcome[T]:
    """Block for one outcome within *timeout_seconds*, then always reap.

    Killing the process from another thread closes the pipe, so this returns
    promptly once the supervisor decides to cancel.
    """
    try:
        if not receive.poll(timeout_seconds):
            raise WorkerTimeoutError(f"isolation worker timed out running {name}")

        try:
            return cast(_Outcome[T], receive.recv())
        except EOFError:
            process.join()
            raise WorkerCrashError(
                f"isolation worker exited with code {process.exitcode} running {name}"
            ) from None
    finally:
        if process.is_alive():
            process.kill()

        process.join()
        receive.close()
        process.close()


async def _drain[T](task: asyncio.Task[_Outcome[T]]) -> None:
    """Await *task* to completion, discarding its outcome or error."""
    try:
        await task
    except BaseException:
        pass


async def run_isolated[T](
    func: Callable[..., T],
    *args: object,
    timeout_seconds: float | None = settings.isolation.timeout_seconds,
) -> T:
    """Run *func* in a bounded, supervised, freshly spawned process.

    *func* must be importable by qualified name (a module-level function) and
    its arguments and return value picklable. A normal exception raised by
    *func* propagates unchanged; a crash that kills the worker is remapped to
    :class:`WorkerCrashError` and a hang past *timeout_seconds* to
    :class:`WorkerTimeoutError`. The blocking process lifecycle runs on a
    thread so the event loop stays free. If the caller is cancelled the worker
    is killed and reaped before the concurrency slot is released, so no process
    outlives the call.

    Args:
        func: Importable module-level callable to execute.
        *args: Picklable positional arguments for *func*.
        timeout_seconds: Maximum execution time, or ``None`` to disable it.

    Returns:
        The callable's picklable return value.

    Raises:
        WorkerCrashError: If the worker exits without returning a result.
        WorkerTimeoutError: If the worker exceeds *timeout_seconds*.
        Exception: Any normal exception raised by *func*.
    """
    # Callable has no typed __qualname__, so read it defensively and fall back
    # to the callable's type name (a partial or __call__ instance lacks one).
    qualname = getattr(func, "__qualname__", None)
    name = qualname if isinstance(qualname, str) else type(func).__qualname__

    async with _worker_slots:
        process, receive = _spawn(func, args, name)
        wait = asyncio.ensure_future(
            asyncio.to_thread(_wait, process, receive, timeout_seconds, name)
        )

        try:
            outcome = cast(_Outcome[T], await asyncio.shield(wait))
        except asyncio.CancelledError:
            if process.is_alive():
                process.kill()

            await shield_to_completion(_drain(wait))
            raise

    if isinstance(outcome, _Failure):
        raise outcome.error

    return outcome.value
