"""Persistent worker-process pool for CPU- and model-bound pipeline stages.

Docling's own guidance for scaling document conversion across cores is
process-level parallelism: several worker processes, each holding its own
converter and models, with documents sharded across them.  Threads give no
benefit here — the heavy stages are torch/native code behind Python that the
GIL serializes, and docling flags its thread-based ``doc_batch_concurrency`` as
experimental with "no benefit expected without free-threaded python".  This
module generalizes the process approach to *every* CPU/model-bound stage —
conversion and chunking alike — behind one primitive, :func:`run_offloaded`.

This is the **persistent, reused** worker policy; its sibling is the
**single-use, timeout-supervised** :func:`hivegent.workers.isolation.run_isolated`
(see :mod:`hivegent.workers` for choosing between them).  The pool trades that
per-call kill for warm engines: a crash-prone or hang-prone untrusted call
belongs in isolation, not here.

Workers are persistent: a :class:`concurrent.futures.ProcessPoolExecutor`
reuses its processes across tasks, so each worker runs the converters' and
chunkers' module-level ``lru_cache`` builders exactly once and reuses the loaded
engine for every later task.  A large batch therefore pays each model-load cost
once per worker, not once per document.

Per-worker CPU threads are budgeted declaratively, not here: each stage sizes
its own accelerator from :attr:`~hivegent.config.ComputeSettings.threads_per_worker`
(the total ``num_threads`` split across ``worker_processes``), so the pool owns
only how many processes exist, never how many threads each one runs.

When ``settings.compute.worker_processes`` is 1 the pool stays dormant and work
runs in-process on a thread guarded by the caller's lock — byte-for-byte the
pre-pool behavior — so single-core deployments and the test suite spawn nothing.
"""

import asyncio
from collections.abc import Callable
from concurrent.futures import ProcessPoolExecutor
from concurrent.futures.process import BrokenProcessPool
from dataclasses import dataclass, field
from multiprocessing import get_context

from ..config import settings

__all__ = ["pipeline_pool", "run_offloaded"]


@dataclass(slots=True)
class ProcessPool:
    """Lazily-created persistent process pool, rebuilt if a worker dies.

    A single instance is shared process-wide (:data:`pipeline_pool`).  The
    executor is created on first use and torn down in the server lifespan; if a
    worker dies mid-task (e.g. a native crash in docling) the whole executor is
    poisoned, so :meth:`run` discards it and retries the task on a fresh pool
    once — turning a crash that used to take down the server into an isolated,
    retryable per-document failure.
    """

    _executor: ProcessPoolExecutor | None = field(default=None, init=False)
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock, init=False)

    @property
    def active(self) -> bool:
        """Whether offloaded work should run in the pool rather than in-process."""
        return settings.compute.worker_processes >= 2

    async def _current(self) -> ProcessPoolExecutor:
        """Return the live executor, creating it on first use."""
        if self._executor is not None:
            return self._executor
        async with self._lock:
            if self._executor is None:
                self._executor = ProcessPoolExecutor(
                    # Reached only via the ``active`` gate (worker_processes >= 2).
                    max_workers=settings.compute.worker_processes,
                    mp_context=get_context("spawn"),
                )
            return self._executor

    async def _discard(self, broken: ProcessPoolExecutor) -> None:
        """Drop *broken* so the next call rebuilds a fresh pool."""
        async with self._lock:
            if self._executor is broken:
                self._executor = None
        broken.shutdown(wait=False, cancel_futures=True)

    async def run[R](self, func: Callable[..., R], /, *args: object) -> R:
        """Run *func(*args)* in a worker process, retrying once on a dead pool."""
        loop = asyncio.get_running_loop()
        executor = await self._current()
        try:
            return await loop.run_in_executor(executor, func, *args)
        except BrokenProcessPool:
            # A worker died. Rebuild and retry once; a genuinely poisonous input
            # crashes again and the error surfaces to the caller (a per-document
            # failure) instead of wedging every later conversion.
            await self._discard(executor)
            retry = await self._current()
            return await loop.run_in_executor(retry, func, *args)

    async def aclose(self) -> None:
        """Shut the pool down, cancelling queued work; safe if never started."""
        async with self._lock:
            executor, self._executor = self._executor, None
        if executor is not None:
            await asyncio.to_thread(executor.shutdown, cancel_futures=True)


pipeline_pool = ProcessPool()


async def run_offloaded[R](
    func: Callable[..., R], /, *args: object, fallback_lock: asyncio.Lock
) -> R:
    """Run a picklable CPU-bound callable off the event loop.

    With the pool active (``worker_processes >= 2``) *func* runs in a persistent
    worker process for true multi-core parallelism across documents; each worker
    owns its own cached engine, so no cross-call lock is needed and *fallback_lock*
    is not taken.  Otherwise *func* runs in a thread guarded by *fallback_lock* —
    the per-base lock that keeps concurrent in-process calls off a shared cached
    engine — exactly as before the pool existed.

    When the pool is active, *func* and *args* must be picklable.  Bound methods
    of the frozen converter/chunker dataclasses qualify: their heavy engine lives
    in a module-level cache, so the pickled instance carries only its config.

    Args:
        func: The CPU-bound callable to run.
        *args: Picklable positional arguments for *func*.
        fallback_lock: Lock guarding the in-process fallback against engine
            races; ignored (a no-op) while the pool is active.

    Returns:
        The callable's return value.
    """
    if pipeline_pool.active:
        return await pipeline_pool.run(func, *args)
    async with fallback_lock:
        return await asyncio.to_thread(func, *args)
