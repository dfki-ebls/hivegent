"""SSE streaming helpers for document routes.

These helpers wrap :mod:`hivegent.workspace` mutations with stage and
progress events for the Vercel AI / Server-Sent Events frontend.  Each
mutation handles its own indexing inline.
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
from ...store import Casebase
from ...types import (
    BulkOperationCompleteEvent,
    BulkOperationProgressEvent,
    LlmConfig,
    OperationErrorEvent,
    OperationStageEvent,
    PipelineSpec,
    UploadCompleteEvent,
)
from ..common import parse_pipeline_spec, prepare_llm_config

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
) -> AsyncGenerator[OperationStageEvent | UploadCompleteEvent | OperationErrorEvent]:
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
    except Exception:
        logger.exception("Upload failed for %s", filepath)
        yield OperationErrorEvent(detail="Upload failed")


async def reconvert_single_stream(
    store: Casebase,
    safe: str,
    spec: PipelineSpec,
    llm_config: LlmConfig,
) -> AsyncGenerator[OperationStageEvent | UploadCompleteEvent | OperationErrorEvent]:
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
    except Exception:
        logger.exception("Reconvert failed for %s", safe)
        yield OperationErrorEvent(detail="Reconvert failed")


async def process_bulk_operation(
    files: list[str],
    process_one: Callable[[str], Awaitable[None]],
    label: str,
) -> AsyncGenerator[BulkOperationProgressEvent | BulkOperationCompleteEvent]:
    """Run a per-file operation over many documents and yield progress.

    Each ``process_one`` call is expected to be a workspace mutation
    that maintains its own index entries; this helper only emits
    progress events.
    """
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

    yield BulkOperationCompleteEvent(
        total_files=total,
        failed_files=failed_files,
        message=f"{label} {total - len(failed_files)} of {total} files",
    )


# ---------------------------------------------------------------------------
# Collection upload request preparation
# ---------------------------------------------------------------------------

_UPLOAD_READ_CHUNK_SIZE = 1024 * 1024


async def validate_collection_upload(
    pipeline_spec: str,
    llm_config: str,
) -> tuple[PipelineSpec, LlmConfig]:
    """Parse and validate pipeline and LLM configuration for collection uploads.

    Async because the SSRF check runs DNS off the event loop.
    """
    spec = parse_pipeline_spec(pipeline_spec)
    resolved = await prepare_llm_config(LlmConfig.model_validate_json(llm_config))
    return spec, resolved


async def read_collection_zip(file: UploadFile) -> bytes:
    """Read and validate a collection ZIP upload."""
    buf = bytearray()
    while chunk := await file.read(_UPLOAD_READ_CHUNK_SIZE):
        buf.extend(chunk)
        if len(buf) > settings.max_collection_size_bytes:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Collection too large. "
                    f"Maximum size: {settings.max_collection_size_bytes} bytes"
                ),
            )
    return bytes(buf)


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
    spec, resolved = await validate_collection_upload(pipeline_spec, llm_config)
    raw = await read_collection_zip(file)
    return PreparedCollection(raw=raw, spec=spec, resolved=resolved)
