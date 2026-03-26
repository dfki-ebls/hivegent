"""Streaming helpers for document operations."""

import logging
from collections.abc import AsyncGenerator, Awaitable, Callable
from typing import Literal

from ...retrieval import mark_dirty_and_sync
from ...store import Casebase
from ...types import BulkOperationCompleteEvent, BulkOperationProgressEvent

__all__ = ["process_bulk_operation"]

logger = logging.getLogger(__name__)


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
