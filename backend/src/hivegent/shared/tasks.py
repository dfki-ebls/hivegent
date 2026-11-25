"""Running work in the background for as long as a scope lasts."""

import asyncio
import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

__all__ = ["background", "cancel_and_drain"]

logger = logging.getLogger(__name__)


async def cancel_and_drain(
    task: asyncio.Task[object], *, log_label: str | None = None
) -> None:
    """Cancel *task*, await its unwind, swallow the ``CancelledError`` it ends with.

    A cancellation of the caller that arrives while it waits still propagates.
    With *log_label*, also warns on non-cancel exceptions so a buggy
    helper task cannot silently mask the wrapped result.
    """
    current = asyncio.current_task()
    cancelling = current.cancelling() if current is not None else 0
    task.cancel()

    try:
        await task
    except asyncio.CancelledError:
        if current is not None and current.cancelling() > cancelling:
            raise
    except Exception:
        if log_label is not None:
            logger.warning("%s errored", log_label, exc_info=True)


@asynccontextmanager
async def background(*tasks: asyncio.Task[object]) -> AsyncGenerator[None]:
    """Keep *tasks* running while the scope lasts, then cancel them and wait for their unwind."""
    try:
        yield
    finally:
        _ = await asyncio.gather(
            *(cancel_and_drain(task, log_label="Background task") for task in tasks)
        )
