"""Cancel non-streaming handler work when the HTTP client disconnects.

Streaming responses already detect disconnect natively (sse-starlette
and Starlette's ``StreamingResponse``).  Plain ``POST`` / ``GET``
handlers that await a single long LLM call do not — :func:`run_until_disconnect`
installs a push-based watcher that blocks on ``request.receive()`` and
cancels the work task on ``http.disconnect``.
"""

import asyncio
import logging
from collections.abc import Awaitable

from fastapi import Request

__all__ = ["run_until_disconnect"]

logger = logging.getLogger(__name__)


async def _cancel_on_disconnect(request: Request, target: asyncio.Task[object]) -> None:
    """Cancel *target* once the ASGI server delivers ``http.disconnect``.

    Push-based: blocks on ``request.receive()`` rather than polling.
    Tolerates ``receive()`` raising (some transports throw when the
    channel has already been torn down) — logs and exits so the failure
    does not silently mask the wrapped result.
    """
    while not target.done():
        try:
            message = await request.receive()
        except Exception:
            logger.warning(
                "receive() failed; disconnect watcher exiting",
                exc_info=True,
            )
            return
        if message["type"] == "http.disconnect":
            target.cancel()
            return


async def _cancel_and_drain(
    task: asyncio.Task[object], *, log_label: str | None = None
) -> None:
    """Cancel *task*, await its unwind, swallow ``CancelledError``.

    With *log_label*, also warns on non-cancel exceptions so a buggy
    helper task cannot silently mask the wrapped result.
    """
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    except Exception:
        if log_label is not None:
            logger.warning("%s errored", log_label, exc_info=True)


async def run_until_disconnect[T](request: Request, work: Awaitable[T]) -> T:
    """Await *work*, cancelling it if the HTTP client disconnects.

    Use inside non-streaming handlers where a long LLM call (or DNS
    pre-flight, or buffered upload read) would otherwise keep running
    after the client aborts.  Also propagates parent cancellation into
    *work* so a server-shutdown or upstream-timeout cancel does not
    leak the inner task.
    """
    task = asyncio.ensure_future(work)
    watcher = asyncio.create_task(_cancel_on_disconnect(request, task))
    try:
        return await task
    finally:
        await _cancel_and_drain(task)
        await _cancel_and_drain(watcher, log_label="Disconnect watcher")
