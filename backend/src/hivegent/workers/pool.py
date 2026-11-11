"""Persistent worker-process pool for CPU- and model-bound pipeline stages.

Conversion and chunking are torch/native code behind the GIL, so processes, not
threads, scale them across cores.  They also isolate native crashes, so a
segfault in docling or torch takes down a worker, never the server, which is
why the pool runs even with a single worker.  A persistent
:class:`ProcessPoolExecutor` reuses its workers, so each pays the
converters'/chunkers' ``lru_cache`` model load once and reuses the warm engine
for every later task.  This is the persistent, reused sibling of the single-use
:func:`hivegent.workers.isolation.run_isolated`, see :mod:`hivegent.workers` for
which to use.

Each worker sizes its own threads from
:attr:`~hivegent.config.ComputeSettings.threads_per_worker`, so the pool owns
only the process count.
"""

import asyncio
from collections.abc import Callable
from concurrent.futures import ProcessPoolExecutor
from concurrent.futures.process import BrokenProcessPool
from dataclasses import dataclass, field
from multiprocessing import get_context

from ..config import settings

__all__ = ["ProcessPool", "limit_torch_threads", "pipeline_pool"]


def limit_torch_threads() -> None:
    """Hold this process's torch to its share of the thread budget.

    Torch defaults to every core, so without this each worker, and the server
    with its own embedding and reranking models, would oversubscribe the cores.
    A no-op when torch is not installed.
    """
    try:
        import torch
    except ImportError:
        return

    torch.set_num_threads(settings.compute.threads_per_worker)


@dataclass(slots=True)
class ProcessPool:
    """Lazily-created persistent process pool, rebuilt if a worker dies.

    A single instance is shared process-wide (:data:`pipeline_pool`).  The
    executor is created on first use and torn down in the server lifespan.  If a
    worker dies mid-task (e.g. a native crash in docling) the whole executor is
    poisoned, so :meth:`run` discards it and retries the task on a fresh pool
    once, turning the crash into an isolated, retryable per-document failure.
    """

    _executor: ProcessPoolExecutor | None = field(default=None, init=False)
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock, init=False)

    async def _current(self) -> ProcessPoolExecutor:
        """Return the live executor, creating it on first use."""
        if self._executor is not None:
            return self._executor

        async with self._lock:
            if self._executor is None:
                self._executor = ProcessPoolExecutor(
                    max_workers=settings.compute.worker_processes,
                    mp_context=get_context("spawn"),
                    initializer=limit_torch_threads,
                )

            return self._executor

    async def _discard(self, broken: ProcessPoolExecutor) -> None:
        """Drop *broken* so the next call rebuilds a fresh pool."""
        async with self._lock:
            if self._executor is broken:
                self._executor = None

        broken.shutdown(wait=False, cancel_futures=True)

    async def run[R](self, func: Callable[..., R], /, *args: object) -> R:
        """Run *func(*args)* in a worker process, retrying once on a dead pool.

        *func* and *args* must be picklable.  Bound methods of the frozen
        converter/chunker dataclasses qualify, since their heavy engine lives in
        a module-level cache and the pickled instance carries only its config.

        Args:
            func: The CPU-bound callable to run.
            *args: Picklable positional arguments for *func*.

        Returns:
            The callable's return value.
        """
        loop = asyncio.get_running_loop()
        executor = await self._current()

        try:
            return await loop.run_in_executor(executor, func, *args)
        except BrokenProcessPool:
            # A worker died. Rebuild and retry once, so a genuinely poisonous
            # input crashes again and surfaces to the caller as a per-document
            # failure instead of wedging every later conversion.
            await self._discard(executor)
            retry = await self._current()
            return await loop.run_in_executor(retry, func, *args)

    async def aclose(self) -> None:
        """Shut the pool down, cancelling queued work, safe if never started."""
        async with self._lock:
            executor, self._executor = self._executor, None

        if executor is not None:
            await asyncio.to_thread(executor.shutdown, cancel_futures=True)


pipeline_pool = ProcessPool()
