"""Streaming helpers for document operations."""

import json
import logging
from collections.abc import AsyncGenerator, Awaitable, Callable
from typing import Literal

from pydantic import BaseModel
from starlette.responses import StreamingResponse

from ...retrieval import mark_dirty_and_sync
from ...store import Casebase
from ...types import BulkOperationCompleteEvent, BulkOperationProgressEvent

__all__ = ["process_bulk_operation", "sse_stream_response"]

logger = logging.getLogger(__name__)


def sse_stream_response(
    generator: AsyncGenerator[BaseModel, None],
) -> StreamingResponse:
    """Wrap an async generator of Pydantic models in an SSE response."""

    async def _event_stream() -> AsyncGenerator[str, None]:
        async for event in generator:
            yield f"data: {json.dumps(event.model_dump())}\n\n"

    return StreamingResponse(
        _event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


async def process_bulk_operation(
    store: Casebase,
    files: list[str],
    process_one: Callable[[str], Awaitable[None]],
    label: str,
) -> AsyncGenerator[BulkOperationProgressEvent | BulkOperationCompleteEvent, None]:
    """Run a per-file operation over multiple documents and yield progress."""
    total = len(files)
    failed_files: list[str] = []

    for index, filepath in enumerate(files):
        status: Literal["ok", "failed"] = "ok"
        try:
            await process_one(filepath)
        except Exception as exc:
            logger.warning("Bulk %s failed for %s: %s", label.lower(), filepath, exc)
            status = "failed"
            failed_files.append(filepath)

        yield BulkOperationProgressEvent(
            file=filepath,
            current=index + 1,
            total=total,
            status=status,
        )

    mark_dirty_and_sync(store)
    yield BulkOperationCompleteEvent(
        total_files=total,
        failed_files=failed_files,
        message=f"{label} {total - len(failed_files)} of {total} files",
    )
