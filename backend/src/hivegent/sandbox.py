"""Process-wide Monty sandbox lifecycle.

The pool of `monty` worker subprocesses the model's Python tool runs in, opened
by the FastAPI lifespan and shared by every request.  Workers are spawned once
and kept warm, so a program costs milliseconds rather than the process start it
would pay for per call, which is also why the pool is owned here rather than by
the tool: a tool instance is built per call.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from pydantic_monty import AsyncMonty

from .config import settings
from .lifespan import LifespanResource

__all__ = ["get_monty_pool", "monty_pool_lifespan"]


@asynccontextmanager
async def _open_pool() -> AsyncIterator[AsyncMonty]:
    """Spawn the configured worker pool and reap it on exit."""
    sandbox = settings.sandbox
    async with AsyncMonty(
        min_processes=sandbox.min_processes,
        max_processes=sandbox.max_processes,
        checkout_timeout=sandbox.checkout_timeout_seconds,
        request_timeout=sandbox.request_timeout_seconds,
    ) as pool:
        yield pool


_shared = LifespanResource("Monty sandbox pool", "monty_pool_lifespan", _open_pool)


@asynccontextmanager
async def monty_pool_lifespan() -> AsyncIterator[None]:
    """Open the process-wide sandbox worker pool for the duration of the context.

    Owned by the FastAPI lifespan: the workers start before the app accepts
    requests and are reaped after the last request has drained.
    """
    async with _shared.lifespan():
        yield


def get_monty_pool() -> AsyncMonty:
    """Return the pooled sandbox the model's Python tool checks sessions out of."""
    return _shared.get()
