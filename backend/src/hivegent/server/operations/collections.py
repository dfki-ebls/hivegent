"""Collection upload helpers for document operations."""

import io
import logging
import tempfile
import zlib
import zipfile
from collections.abc import AsyncGenerator
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from fastapi import Form, HTTPException, UploadFile

from ...config import sanitize_document_path, settings
from ...converters.base import DOCUMENT_EXTENSION
from ...converters.wikilinks import preprocess_markdown
from ...entries import entry_exists, stem_path_from_reference
from ...retrieval import mark_dirty_and_sync
from ...store import Casebase
from ...types import CollectionCompleteEvent, CollectionProgressEvent, LlmConfig
from ..common import parse_pipeline_spec, resolve_llm_config
from ..models import PipelineSpec
from .uploads import upload_file

__all__ = [
    "PreparedCollection",
    "prepare_collection_upload",
    "process_collection",
    "read_collection_zip",
    "validate_collection_upload",
]

logger = logging.getLogger(__name__)


async def process_collection(
    store: Casebase,
    raw: bytes,
    spec: PipelineSpec,
    resolved: LlmConfig,
) -> AsyncGenerator[CollectionProgressEvent | CollectionCompleteEvent, None]:
    """Process a ZIP collection and yield progress events for each file."""
    failed: list[str] = []
    markdown_count = 0
    converted_count = 0
    current = 0

    with tempfile.TemporaryDirectory() as tmp_dir:
        extract_root = Path(tmp_dir)

        try:
            with zipfile.ZipFile(io.BytesIO(raw)) as archive:
                for info in archive.infolist():
                    normalized = str(PurePosixPath(info.filename))
                    if (
                        normalized.startswith("/")
                        or normalized.startswith("..")
                        or "/.." in normalized
                    ):
                        raise HTTPException(
                            status_code=400,
                            detail=f"ZIP contains unsafe path: {info.filename}",
                        )
                    if (
                        info.file_size > settings.max_file_size_bytes
                        and not info.is_dir()
                    ):
                        raise HTTPException(
                            status_code=400,
                            detail=(
                                f"File '{info.filename}' in ZIP is too large "
                                f"({info.file_size} bytes decompressed). "
                                f"Maximum: {settings.max_file_size_bytes} bytes"
                            ),
                        )
                archive.extractall(extract_root)
        except zipfile.BadZipFile as exc:
            raise HTTPException(status_code=400, detail="Invalid ZIP file") from exc
        except zlib.error as exc:
            raise HTTPException(
                status_code=400,
                detail=f"Failed to decompress ZIP: {exc!s}",
            ) from exc

        top_items = list(extract_root.iterdir())
        if len(top_items) == 1 and top_items[0].is_dir():
            extract_root = top_items[0]

        collection_files = sorted(
            str(path.relative_to(extract_root).as_posix())
            for path in extract_root.rglob("*")
            if path.is_file()
        )
        if len(collection_files) > settings.max_collection_files:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Collection has too many files ({len(collection_files)}). "
                    f"Maximum: {settings.max_collection_files}"
                ),
            )

        workspace_dir = store.workspace_dir(settings.data_dir)
        metadata_dir = store.metadata_dir(settings.data_dir)
        preprocessed_markdown: dict[str, bytes] = {}
        collection_stems: set[str] = set()
        # Binary files whose stem already has a companion markdown description.
        # These are written as originals alongside the markdown rather than
        # processed independently.
        companion_originals: set[str] = set()

        for relative_path in collection_files:
            safe = sanitize_document_path(relative_path)
            suffix = PurePosixPath(safe).suffix.lower()
            if suffix == DOCUMENT_EXTENSION:
                try:
                    text = (extract_root / relative_path).read_text(encoding="utf-8")
                except Exception as exc:
                    logger.warning("Failed to read %s: %s", relative_path, exc)
                    failed.append(relative_path)
                    continue
                normalized = preprocess_markdown(
                    text, safe, frozenset(collection_files)
                )
                preprocessed_markdown[safe] = normalized.content.encode("utf-8")

            stem = stem_path_from_reference(safe)
            if entry_exists(workspace_dir, metadata_dir, safe):
                failed.append(relative_path)
                continue
            if stem in collection_stems:
                if suffix != DOCUMENT_EXTENSION:
                    # A markdown with this stem was already registered; keep the
                    # binary as a companion original instead of failing it.
                    companion_originals.add(relative_path)
                else:
                    failed.append(relative_path)
                continue
            collection_stems.add(stem)

        total = len(collection_files)
        try:
            for relative_path in collection_files:
                safe = sanitize_document_path(relative_path)
                if relative_path in failed:
                    current += 1
                    yield CollectionProgressEvent(
                        file=relative_path,
                        current=current,
                        total=total,
                        status="failed",
                    )
                    continue

                if relative_path in companion_originals:
                    # Store the binary as the original for its markdown sibling.
                    try:
                        original_bytes = (extract_root / relative_path).read_bytes()
                        original_path = workspace_dir / safe
                        original_path.parent.mkdir(parents=True, exist_ok=True)
                        original_path.write_bytes(original_bytes)
                        status = "ok"
                    except Exception as exc:
                        logger.warning(
                            "Failed to write original %s: %s", relative_path, exc
                        )
                        failed.append(relative_path)
                        status = "failed"
                    current += 1
                    yield CollectionProgressEvent(
                        file=relative_path,
                        current=current,
                        total=total,
                        status=status,
                    )
                    continue

                try:
                    if safe in preprocessed_markdown:
                        content_bytes = preprocessed_markdown[safe]
                        markdown_count += 1
                    else:
                        content_bytes = (extract_root / relative_path).read_bytes()
                        converted_count += 1
                    await upload_file(
                        store,
                        safe,
                        content_bytes,
                        spec,
                        resolved,
                        origin="collection",
                        sync=False,
                    )
                    status = "ok"
                except Exception as exc:
                    logger.warning("Failed to process %s: %s", relative_path, exc)
                    if safe in preprocessed_markdown:
                        markdown_count -= 1
                    else:
                        converted_count -= 1
                    failed.append(relative_path)
                    status = "failed"

                current += 1
                yield CollectionProgressEvent(
                    file=relative_path,
                    current=current,
                    total=total,
                    status=status,
                )
        finally:
            # Runs on normal completion, exception, and client disconnect
            # (GeneratorExit / CancelledError) so the index reflects any
            # files already persisted to disk.
            mark_dirty_and_sync(store)

    total_ok = markdown_count + converted_count
    yield CollectionCompleteEvent(
        total_files=total_ok,
        markdown_files=markdown_count,
        converted_attachments=converted_count,
        failed_files=failed,
        message=(
            f"Collection uploaded: {markdown_count} markdown, "
            f"{converted_count} processed attachments"
            + (f", {len(failed)} failed" if failed else "")
        ),
    )


def validate_collection_upload(
    pipeline_spec: str,
    llm_config: str,
) -> tuple[PipelineSpec, LlmConfig]:
    """Parse and validate pipeline and LLM configuration for collection uploads."""
    spec = parse_pipeline_spec(pipeline_spec)
    llm = LlmConfig.model_validate_json(llm_config)
    resolved = resolve_llm_config(llm, default_model=settings.llm.vision_model)
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
    """FastAPI dependency that parses config and buffers the ZIP upload.

    Runs before the response body starts, so validation errors surface as a
    normal 400 response instead of truncating an SSE stream.
    """
    spec, resolved = validate_collection_upload(pipeline_spec, llm_config)
    raw = await read_collection_zip(file)
    return PreparedCollection(raw=raw, spec=spec, resolved=resolved)
