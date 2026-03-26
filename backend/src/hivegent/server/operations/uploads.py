"""Upload and reconversion helpers for document operations."""

import logging
import mimetypes
import shutil
from collections.abc import AsyncGenerator
from pathlib import Path, PurePosixPath

from fastapi import HTTPException
from pydantic import ValidationError

from ...chunkers.base import (
    EntryGeneratedBy,
    EntryKind,
    EntryMetadata,
    EntryOrigin,
)
from ...chunks import chunk_document, get_metadata
from ...config import settings
from ...converters import (
    ConversionPipeline,
    get_converter,
    resolve_auto_pipeline,
)
from ...converters.alt_text import describe_image
from ...converters.base import DOCUMENT_EXTENSION, IMAGE_EXTENSIONS
from ...entries import (
    assets_dir_for_stem,
    description_path_for_stem,
    stem_path_from_reference,
)
from ...retrieval import mark_dirty_and_sync
from ...store import Casebase
from ...types import (
    LlmConfig,
    OperationErrorEvent,
    OperationStageEvent,
    UploadCompleteEvent,
)
from ..common import cleanup_empty_parents, resolve_llm_config
from ..models import PipelineSpec

__all__ = [
    "reconvert_single",
    "reconvert_single_stream",
    "upload_file",
    "upload_file_stream",
]

logger = logging.getLogger(__name__)


def _is_markdown(suffix: str) -> bool:
    """Check whether a file extension is markdown."""
    return suffix.lower() == DOCUMENT_EXTENSION


def _is_image(suffix: str) -> bool:
    """Check whether a file extension is a known image type."""
    return suffix.lower() in IMAGE_EXTENSIONS


def _resolve_vision_config(llm_config: LlmConfig) -> LlmConfig | None:
    """Build an LLM config for the vision model when available."""
    resolved = resolve_llm_config(llm_config, default_model=settings.llm.vision_model)
    return resolved if resolved.model else None


def _build_entry_metadata(
    *,
    stem_path: str,
    description_path: str,
    original_path: str | None,
    assets_dir: str | None,
    entry_kind: EntryKind,
    origin: EntryOrigin,
    generated_by: EntryGeneratedBy,
) -> EntryMetadata:
    """Build metadata for a logical entry."""
    files = [description_path]
    if original_path is not None:
        files.append(original_path)
    return EntryMetadata(
        entry_kind=entry_kind,
        stem_path=stem_path,
        description_path=description_path,
        original_path=original_path,
        assets_dir=assets_dir,
        mime=mimetypes.guess_type(original_path or description_path)[0],
        origin=origin,
        generated_by=generated_by,
        files=files,
    )


def _write_original_file(workspace_dir: Path, filepath: str, content: bytes) -> Path:
    """Write an original file into the workspace."""
    full_path = workspace_dir / filepath
    full_path.parent.mkdir(parents=True, exist_ok=True)
    full_path.write_bytes(content)
    return full_path


async def _write_markdown_projection(
    store: Casebase,
    description_path: str,
    content: str,
    spec: PipelineSpec,
    *,
    entry_metadata: EntryMetadata,
) -> tuple[int, str]:
    """Write markdown content and persist chunk metadata."""
    workspace_dir = store.workspace_dir(settings.data_dir)
    full_path = workspace_dir / description_path
    full_path.parent.mkdir(parents=True, exist_ok=True)
    full_path.write_text(content, encoding="utf-8")
    chunked = await chunk_document(
        store,
        description_path,
        content,
        spec.chunking,
        entry_metadata=entry_metadata,
    )
    return len(chunked.chunks), chunked.pipeline


async def _build_image_description(
    filepath: str,
    content: bytes,
    llm_config: LlmConfig,
) -> str:
    """Generate markdown text for an image description."""
    vision = _resolve_vision_config(llm_config)
    fallback = PurePosixPath(filepath).stem
    if not vision:
        return f"{fallback}\n"

    media_type = mimetypes.guess_type(filepath)[0]
    if not media_type or not media_type.startswith("image/"):
        return f"{fallback}\n"

    try:
        description = await describe_image(content, media_type, vision)
    except Exception:
        logger.warning("Image description generation failed for %s", filepath, exc_info=True)
        description = fallback
    return f"{description.strip() or fallback}\n"


def _build_binary_stub(filepath: str, size_bytes: int) -> str:
    """Build a minimal searchable markdown stub for an opaque binary file."""
    name = PurePosixPath(filepath).name
    mime = mimetypes.guess_type(filepath)[0] or "application/octet-stream"
    return f"File name: {name}.\nMIME type: {mime}.\nSize: {size_bytes} bytes.\n"


def _clear_assets_subtree(store: Casebase, stem_path: str) -> None:
    """Delete an entry's child-assets subtree from workspace and metadata."""
    workspace_dir = store.workspace_dir(settings.data_dir)
    metadata_dir = store.metadata_dir(settings.data_dir)
    assets_dir = assets_dir_for_stem(stem_path)
    workspace_assets = workspace_dir / assets_dir
    if workspace_assets.exists():
        shutil.rmtree(workspace_assets)
        cleanup_empty_parents(workspace_assets, workspace_dir)
    metadata_assets = metadata_dir / assets_dir
    if metadata_assets.exists():
        shutil.rmtree(metadata_assets)
        cleanup_empty_parents(metadata_assets, metadata_dir)


async def _upload_markdown(
    store: Casebase,
    filepath: str,
    content: bytes,
    spec: PipelineSpec,
    *,
    origin: EntryOrigin,
) -> UploadCompleteEvent:
    """Store a user-authored markdown file."""
    workspace_dir = store.workspace_dir(settings.data_dir)
    text_content = content.decode("utf-8")
    stem_path = stem_path_from_reference(filepath)
    chunk_count, chunking_used = await _write_markdown_projection(
        store,
        filepath,
        text_content,
        spec,
        entry_metadata=_build_entry_metadata(
            stem_path=stem_path,
            description_path=filepath,
            original_path=None,
            assets_dir=assets_dir_for_stem(stem_path)
            if (workspace_dir / assets_dir_for_stem(stem_path)).exists()
            else None,
            entry_kind="user_markdown",
            origin=origin,
            generated_by="user",
        ),
    )
    return UploadCompleteEvent(
        filename=filepath,
        converted_filename=None,
        size_bytes=len(content),
        chunk_count=chunk_count,
        chunking_pipeline_used=chunking_used,
        message="Document uploaded successfully",
    )


async def _upload_image(
    store: Casebase,
    filepath: str,
    content: bytes,
    spec: PipelineSpec,
    llm_config: LlmConfig,
    *,
    origin: EntryOrigin,
) -> UploadCompleteEvent:
    """Store an image and generate a markdown description."""
    workspace_dir = store.workspace_dir(settings.data_dir)
    _write_original_file(workspace_dir, filepath, content)
    stem_path = stem_path_from_reference(filepath)
    description_path = description_path_for_stem(stem_path)
    markdown_content = await _build_image_description(filepath, content, llm_config)
    chunk_count, chunking_used = await _write_markdown_projection(
        store,
        description_path,
        markdown_content,
        spec,
        entry_metadata=_build_entry_metadata(
            stem_path=stem_path,
            description_path=description_path,
            original_path=filepath,
            assets_dir=None,
            entry_kind="image",
            origin=origin,
            generated_by="vision",
        ),
    )
    return UploadCompleteEvent(
        filename=filepath,
        converted_filename=description_path,
        size_bytes=len(content),
        chunk_count=chunk_count,
        chunking_pipeline_used=chunking_used,
        message="Image uploaded and described successfully",
    )


async def _upload_binary_stub(
    store: Casebase,
    filepath: str,
    content: bytes,
    spec: PipelineSpec,
    *,
    origin: EntryOrigin,
    original_written: bool = False,
) -> UploadCompleteEvent:
    """Store a non-convertible binary with a minimal markdown stub."""
    workspace_dir = store.workspace_dir(settings.data_dir)
    if not original_written:
        _write_original_file(workspace_dir, filepath, content)
    stem_path = stem_path_from_reference(filepath)
    description_path = description_path_for_stem(stem_path)
    markdown_content = _build_binary_stub(filepath, len(content))
    chunk_count, chunking_used = await _write_markdown_projection(
        store,
        description_path,
        markdown_content,
        spec,
        entry_metadata=_build_entry_metadata(
            stem_path=stem_path,
            description_path=description_path,
            original_path=filepath,
            assets_dir=None,
            entry_kind="binary_stub",
            origin=origin,
            generated_by="stub",
        ),
    )
    return UploadCompleteEvent(
        filename=filepath,
        converted_filename=description_path,
        size_bytes=len(content),
        chunk_count=chunk_count,
        chunking_pipeline_used=chunking_used,
        message="Binary file uploaded with searchable stub",
    )


async def _upload_convertible(
    store: Casebase,
    filepath: str,
    content: bytes,
    spec: PipelineSpec,
    llm_config: LlmConfig,
    *,
    origin: EntryOrigin,
) -> UploadCompleteEvent:
    """Store a convertible binary, convert it, and process extracted assets."""
    workspace_dir = store.workspace_dir(settings.data_dir)
    original_full_path = _write_original_file(workspace_dir, filepath, content)
    basename = PurePosixPath(filepath).name
    conversion_pipeline = spec.conversion.pipeline

    try:
        converter = get_converter(
            conversion_pipeline,
            filename=basename,
            config=spec.conversion.config,
            llm_options=llm_config,
        )
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=exc.errors()) from exc
    except (ImportError, ValueError) as exc:
        if conversion_pipeline == ConversionPipeline.AUTO:
            return await _upload_binary_stub(
                store,
                filepath,
                content,
                spec,
                origin=origin,
                original_written=True,
            )
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    resolved_conversion = conversion_pipeline
    if conversion_pipeline == ConversionPipeline.AUTO:
        resolved_conversion = resolve_auto_pipeline(basename)

    try:
        result = await converter(original_full_path)
    except Exception as exc:
        if conversion_pipeline == ConversionPipeline.AUTO:
            logger.warning("Falling back to stub markdown for %s: %s", filepath, exc)
            return await _upload_binary_stub(
                store,
                filepath,
                content,
                spec,
                origin=origin,
                original_written=True,
            )
        raise HTTPException(
            status_code=500,
            detail=f"Conversion failed: {exc!s}",
        ) from exc

    stem_path = stem_path_from_reference(filepath)
    description_path = description_path_for_stem(stem_path)
    assets_dir = assets_dir_for_stem(stem_path)
    markdown_content = result.markdown
    has_assets = False

    for image_relpath, image_data in sorted(result.images.items()):
        child_path = str((PurePosixPath(assets_dir) / image_relpath).as_posix())
        relative_from_doc = str(
            PurePosixPath(PurePosixPath(assets_dir).name) / PurePosixPath(image_relpath)
        )
        markdown_content = markdown_content.replace(image_relpath, relative_from_doc)
        if spec.process_assets:
            await upload_file(
                store,
                child_path,
                image_data,
                spec,
                llm_config,
                origin="extracted",
                sync=False,
            )
        else:
            _write_original_file(workspace_dir, child_path, image_data)
        has_assets = True

    chunk_count, chunking_used = await _write_markdown_projection(
        store,
        description_path,
        markdown_content,
        spec,
        entry_metadata=_build_entry_metadata(
            stem_path=stem_path,
            description_path=description_path,
            original_path=filepath,
            assets_dir=assets_dir if has_assets else None,
            entry_kind="convertible",
            origin=origin,
            generated_by="converter",
        ),
    )
    return UploadCompleteEvent(
        filename=filepath,
        converted_filename=description_path,
        size_bytes=len(content),
        conversion_pipeline_used=resolved_conversion.value,
        chunk_count=chunk_count,
        chunking_pipeline_used=chunking_used,
        message="Document uploaded and converted successfully",
    )


async def upload_file(
    store: Casebase,
    filepath: str,
    content: bytes,
    spec: PipelineSpec,
    llm_config: LlmConfig,
    *,
    origin: EntryOrigin = "upload",
    sync: bool = True,
) -> UploadCompleteEvent:
    """Upload a single file using the recursive stem-entry model."""
    suffix = PurePosixPath(filepath).suffix.lower()

    if _is_markdown(suffix):
        result = await _upload_markdown(store, filepath, content, spec, origin=origin)
    elif _is_image(suffix):
        result = await _upload_image(
            store, filepath, content, spec, llm_config, origin=origin
        )
    else:
        result = await _upload_convertible(
            store,
            filepath,
            content,
            spec,
            llm_config,
            origin=origin,
        )

    if sync:
        mark_dirty_and_sync(store)
    return result


async def reconvert_single(
    store: Casebase,
    safe: str,
    spec: PipelineSpec,
    resolved: LlmConfig,
) -> UploadCompleteEvent:
    """Reprocess a logical entry from its original file."""
    metadata = get_metadata(store, safe)
    if not metadata or not metadata.original_path:
        raise HTTPException(
            status_code=404,
            detail=f"No original file found for '{safe}'",
        )

    workspace_dir = store.workspace_dir(settings.data_dir)
    original_path = workspace_dir / metadata.original_path
    if not original_path.exists():
        raise HTTPException(
            status_code=404,
            detail=f"No original file found for '{safe}'",
        )

    _clear_assets_subtree(store, metadata.stem_path)
    result = await upload_file(
        store,
        metadata.original_path,
        original_path.read_bytes(),
        spec,
        resolved,
        origin=metadata.origin,
        sync=False,
    )
    mark_dirty_and_sync(store)
    return result


async def upload_file_stream(
    store: Casebase,
    filepath: str,
    content: bytes,
    spec: PipelineSpec,
    llm_config: LlmConfig,
) -> AsyncGenerator[OperationStageEvent | UploadCompleteEvent | OperationErrorEvent, None]:
    """Upload a single file with SSE stage events for progress."""
    suffix = PurePosixPath(filepath).suffix.lower()
    try:
        if _is_markdown(suffix):
            yield OperationStageEvent(stage="Chunking document")
        elif _is_image(suffix):
            yield OperationStageEvent(stage="Generating image description")
        else:
            yield OperationStageEvent(stage="Processing document")
        result = await upload_file(store, filepath, content, spec, llm_config)
        yield result
    except HTTPException as exc:
        yield OperationErrorEvent(detail=str(exc.detail))
    except Exception as exc:
        yield OperationErrorEvent(detail=str(exc))


async def reconvert_single_stream(
    store: Casebase,
    safe: str,
    spec: PipelineSpec,
    resolved: LlmConfig,
) -> AsyncGenerator[OperationStageEvent | UploadCompleteEvent | OperationErrorEvent, None]:
    """Reconvert a single document with SSE stage events for progress."""
    try:
        metadata = get_metadata(store, safe)
        if metadata and metadata.original_path:
            original_suffix = PurePosixPath(metadata.original_path).suffix.lower()
            if _is_image(original_suffix):
                yield OperationStageEvent(stage="Regenerating image description")
            else:
                yield OperationStageEvent(stage="Reprocessing document")
        else:
            yield OperationStageEvent(stage="Reprocessing document")
        result = await reconvert_single(store, safe, spec, resolved)
        yield result
    except HTTPException as exc:
        yield OperationErrorEvent(detail=str(exc.detail))
    except Exception as exc:
        yield OperationErrorEvent(detail=str(exc))
