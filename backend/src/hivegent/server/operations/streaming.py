"""SSE streaming helpers for document routes.

These helpers wrap :mod:`hivegent.workspace` mutations with stage and
progress events for the Vercel AI / Server-Sent Events frontend.  They
do *not* mark the search index dirty — every workspace mutation already
does so internally.
"""

import logging
from collections.abc import AsyncGenerator, Awaitable, Callable
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Literal

from fastapi import Form, HTTPException, UploadFile

from ... import workspace
from ...chunks import get_metadata
from ...config import settings
from ...converters.base import is_image_suffix, is_markdown_suffix
from ...retrieval import mark_dirty_and_sync
from ...store import Casebase
from ...types import (
    BulkOperationCompleteEvent,
    BulkOperationProgressEvent,
    LlmConfig,
    OperationErrorEvent,
    OperationStageEvent,
    PipelineSpec,
    UploadCompleteEvent,
    resolve_llm_config,
)
from ..common import parse_pipeline_spec

__all__ = [
    "PreparedCollection",
    "prepare_collection_upload",
    "process_bulk_operation",
    "read_collection_zip",
    "reconvert_single_stream",
    "upload_file_stream",
    "validate_collection_upload",
]

logger = logging.getLogger(__name__)


async def upload_file_stream(
    store: Casebase,
    filepath: str,
    content: bytes,
    spec: PipelineSpec,
    llm_config: LlmConfig,
    *,
    overwrite: bool = False,
) -> AsyncGenerator[
    OperationStageEvent | UploadCompleteEvent | OperationErrorEvent, None
]:
    """Upload a single file with SSE stage events for progress."""
    suffix = PurePosixPath(filepath).suffix.lower()
    try:
        if is_markdown_suffix(suffix):
            yield OperationStageEvent(stage="Chunking document")
        elif is_image_suffix(suffix):
            yield OperationStageEvent(stage="Generating image description")
        else:
            yield OperationStageEvent(stage="Processing document")
        result = await workspace.upload(
            store,
            filepath,
            content,
            spec=spec,
            llm=llm_config,
            overwrite=overwrite,
        )
        yield result
    except HTTPException as exc:
        yield OperationErrorEvent(detail=str(exc.detail))
    except Exception as exc:
        yield OperationErrorEvent(detail=str(exc))


async def reconvert_single_stream(
    store: Casebase,
    safe: str,
    spec: PipelineSpec,
    llm_config: LlmConfig,
) -> AsyncGenerator[
    OperationStageEvent | UploadCompleteEvent | OperationErrorEvent, None
]:
    """Re-convert a single document with SSE stage events for progress."""
    try:
        metadata = get_metadata(store, safe)
        if metadata and metadata.original_path:
            original_suffix = PurePosixPath(metadata.original_path).suffix.lower()
            if is_image_suffix(original_suffix):
                yield OperationStageEvent(stage="Regenerating image description")
            else:
                yield OperationStageEvent(stage="Reprocessing document")
        else:
            yield OperationStageEvent(stage="Reprocessing document")
        result = await workspace.reconvert(store, safe, spec=spec, llm=llm_config)
        yield result
    except HTTPException as exc:
        yield OperationErrorEvent(detail=str(exc.detail))
    except Exception as exc:
        yield OperationErrorEvent(detail=str(exc))


async def process_bulk_operation(
    store: Casebase,
    files: list[str],
    process_one: Callable[[str], Awaitable[None]],
    label: str,
) -> AsyncGenerator[BulkOperationProgressEvent | BulkOperationCompleteEvent, None]:
    """Run a per-file operation over many documents and yield progress.

    ``process_one`` callbacks must invoke their workspace mutation with
    ``sync=False``; this helper performs a single coalesced index sync
    after the loop, avoiding ``O(N²)`` rebuilds for large bulks.
    """
    total = len(files)
    failed_files: list[str] = []

    try:
        for index, filepath in enumerate(files):
            status: Literal["ok", "failed"] = "ok"
            try:
                await process_one(filepath)
            except Exception as exc:
                logger.warning(
                    "Bulk %s failed for %s: %s", label.lower(), filepath, exc
                )
                status = "failed"
                failed_files.append(filepath)

            yield BulkOperationProgressEvent(
                file=filepath,
                current=index + 1,
                total=total,
                status=status,
            )
    finally:
        mark_dirty_and_sync(store)

    yield BulkOperationCompleteEvent(
        total_files=total,
        failed_files=failed_files,
        message=f"{label} {total - len(failed_files)} of {total} files",
    )


# ---------------------------------------------------------------------------
# Collection upload request preparation
# ---------------------------------------------------------------------------


def validate_collection_upload(
    pipeline_spec: str,
    llm_config: str,
) -> tuple[PipelineSpec, LlmConfig]:
    """Parse and validate pipeline and LLM configuration for collection uploads."""
    spec = parse_pipeline_spec(pipeline_spec)
    llm = LlmConfig.model_validate_json(llm_config)
    resolved = resolve_llm_config(llm, default_model=settings.llm.aux_model)
    return spec, resolved


async def read_collection_zip(file: UploadFile) -> bytes:
    """Read and validate a collection ZIP upload."""
    raw = await file.read()
    if len(raw) > settings.max_collection_size_bytes:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Collection too large. "
                f"Maximum size: {settings.max_collection_size_bytes} bytes"
            ),
        )
    return raw


@dataclass(slots=True, frozen=True)
class PreparedCollection:
    """Validated collection payload ready for streaming."""

    raw: bytes
    spec: PipelineSpec
    resolved: LlmConfig


async def prepare_collection_upload(
    file: UploadFile,
    pipeline_spec: str = Form(default="{}"),
    llm_config: str = Form(default="{}"),
) -> PreparedCollection:
    """FastAPI dependency that parses config and buffers the ZIP upload."""
    spec, resolved = validate_collection_upload(pipeline_spec, llm_config)
    raw = await read_collection_zip(file)
    return PreparedCollection(raw=raw, spec=spec, resolved=resolved)
