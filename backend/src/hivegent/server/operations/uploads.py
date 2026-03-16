"""Upload and reconversion helpers for document operations."""

import logging
import mimetypes
from collections.abc import AsyncGenerator
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from fastapi import HTTPException
from pydantic import BaseModel, ValidationError

from ...chunks import chunk_document, get_metadata
from ...config import settings
from ...converters import (
    ConversionPipeline,
    ConversionResult,
    get_converter,
    resolve_auto_pipeline,
)
from ...converters.alt_text import describe_image, generate_alt_texts
from ...converters.base import DOCUMENT_EXTENSION, IMAGE_EXTENSIONS
from ...retrieval import mark_dirty_and_sync
from ...store import Casebase
from ...types import (
    LlmConfig,
    OperationErrorEvent,
    OperationStageEvent,
    UploadCompleteEvent,
    UploadDocumentResponse,
)
from ..common import cleanup_empty_parents, resolve_llm_config
from .files import find_original
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


@dataclass(slots=True, frozen=True)
class _ImageStoreResult:
    """Result of storing extracted images from a conversion."""

    markdown: str
    workspace_paths: list[str]
    alt_text_images: dict[str, bytes]


def _store_conversion_images(
    result: ConversionResult,
    workspace_dir: Path,
    doc_relpath: str,
) -> _ImageStoreResult:
    """Store extracted images in the workspace and rewrite markdown paths."""
    markdown = result.markdown
    if not result.images:
        return _ImageStoreResult(
            markdown=markdown,
            workspace_paths=[],
            alt_text_images={},
        )

    doc_path = PurePosixPath(doc_relpath)
    base_name = doc_path.stem
    parent_str = str(doc_path.parent)
    assets_prefix = f"{base_name}_assets"
    if parent_str != ".":
        assets_prefix = f"{parent_str}/{base_name}_assets"

    workspace_paths: list[str] = []
    alt_text_images: dict[str, bytes] = {}

    for image_relpath, image_data in result.images.items():
        image_filename = PurePosixPath(image_relpath).name
        workspace_image_path = f"{assets_prefix}/{image_filename}"
        workspace_image_full = workspace_dir / workspace_image_path
        workspace_image_full.parent.mkdir(parents=True, exist_ok=True)
        workspace_image_full.write_bytes(image_data)
        workspace_paths.append(workspace_image_path)
        local_path = f"{base_name}_assets/{image_filename}"
        markdown = markdown.replace(image_relpath, local_path)
        alt_text_images[local_path] = image_data

    return _ImageStoreResult(
        markdown=markdown,
        workspace_paths=workspace_paths,
        alt_text_images=alt_text_images,
    )


async def upload_file(
    store: Casebase,
    filepath: str,
    content: bytes,
    spec: PipelineSpec,
    llm_config: LlmConfig,
) -> UploadDocumentResponse:
    """Upload a single file, converting binary files to markdown."""
    basename = filepath.rsplit("/", 1)[-1] if "/" in filepath else filepath
    suffix = "." + basename.rsplit(".", 1)[-1].lower() if "." in basename else ""
    workspace_dir = store.workspace_dir(settings.data_dir)

    if _is_markdown(suffix):
        file_path = workspace_dir / filepath
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_bytes(content)

        chunk_count = None
        chunking_used = None
        try:
            text_content = content.decode("utf-8")
            chunked = await chunk_document(store, filepath, text_content, spec.chunking)
            chunk_count = len(chunked.chunks)
            chunking_used = chunked.pipeline
            mark_dirty_and_sync(store)
        except Exception as exc:
            logger.warning("Chunking failed for %s: %s", filepath, exc)

        return UploadDocumentResponse(
            filename=filepath,
            size_bytes=len(content),
            chunk_count=chunk_count,
            chunking_pipeline_used=chunking_used,
            message="Document uploaded successfully",
        )

    if _is_image(suffix):
        image_workspace = workspace_dir / filepath
        image_workspace.parent.mkdir(parents=True, exist_ok=True)
        image_workspace.write_bytes(content)

        originals_dir = store.originals_dir(settings.data_dir)
        original_path = originals_dir / filepath
        original_path.parent.mkdir(parents=True, exist_ok=True)
        original_path.write_bytes(content)

        vision = _resolve_vision_config(llm_config)
        alt_text = PurePosixPath(filepath).stem
        if vision:
            media_type = mimetypes.guess_type(filepath)[0]
            if media_type and media_type.startswith("image/"):
                try:
                    alt_text = await describe_image(content, media_type, vision)
                except Exception:
                    logger.warning("Alt text generation failed for %s", filepath)

        base_name = basename.rsplit(".", 1)[0]
        if "/" in filepath:
            parent_dir = filepath.rsplit("/", 1)[0]
            converted_relpath = f"{parent_dir}/{base_name}.md"
        else:
            converted_relpath = f"{base_name}.md"

        wrapper_content = f"![{alt_text}]({basename})\n"
        converted_path = workspace_dir / converted_relpath
        converted_path.parent.mkdir(parents=True, exist_ok=True)
        converted_path.write_text(wrapper_content, encoding="utf-8")

        chunk_count = None
        chunking_used = None
        try:
            chunked = await chunk_document(
                store,
                converted_relpath,
                wrapper_content,
                spec.chunking,
                images=[filepath],
            )
            chunk_count = len(chunked.chunks)
            chunking_used = chunked.pipeline
            mark_dirty_and_sync(store)
        except Exception as exc:
            logger.warning("Chunking failed for %s: %s", converted_relpath, exc)

        return UploadDocumentResponse(
            filename=filepath,
            converted_filename=converted_relpath,
            size_bytes=len(content),
            chunk_count=chunk_count,
            chunking_pipeline_used=chunking_used,
            message="Image uploaded with wrapper markdown",
        )

    originals_dir = store.originals_dir(settings.data_dir)
    original_path = originals_dir / filepath
    original_path.parent.mkdir(parents=True, exist_ok=True)
    original_path.write_bytes(content)

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
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    resolved_conversion = conversion_pipeline
    if conversion_pipeline == ConversionPipeline.AUTO:
        resolved_conversion = resolve_auto_pipeline(basename)

    try:
        result = await converter(original_path)
    except ImportError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Conversion failed: {exc!s}",
        ) from exc

    base_name = basename.rsplit(".", 1)[0]
    if "/" in filepath:
        parent_dir = filepath.rsplit("/", 1)[0]
        converted_relpath = f"{parent_dir}/{base_name}.md"
    else:
        converted_relpath = f"{base_name}.md"

    image_result = _store_conversion_images(result, workspace_dir, converted_relpath)
    markdown_content = image_result.markdown
    vision = _resolve_vision_config(llm_config)
    try:
        markdown_content = await generate_alt_texts(
            markdown_content,
            image_result.alt_text_images,
            vision,
        )
    except Exception:
        logger.warning("Alt text generation failed for %s", converted_relpath)

    converted_path = workspace_dir / converted_relpath
    converted_path.parent.mkdir(parents=True, exist_ok=True)
    converted_path.write_text(markdown_content, encoding="utf-8")

    chunk_count = None
    chunking_used = None
    try:
        chunked = await chunk_document(
            store,
            converted_relpath,
            markdown_content,
            spec.chunking,
            images=image_result.workspace_paths,
        )
        chunk_count = len(chunked.chunks)
        chunking_used = chunked.pipeline
        mark_dirty_and_sync(store)
    except Exception as exc:
        logger.warning("Chunking failed for %s: %s", converted_relpath, exc)

    return UploadDocumentResponse(
        filename=filepath,
        converted_filename=converted_relpath,
        size_bytes=len(content),
        conversion_pipeline_used=resolved_conversion.value,
        chunk_count=chunk_count,
        chunking_pipeline_used=chunking_used,
        message="Document uploaded and converted successfully",
    )


async def reconvert_single(
    store: Casebase,
    safe: str,
    spec: PipelineSpec,
    resolved: LlmConfig,
) -> UploadDocumentResponse:
    """Reconvert a single document from its original binary file."""
    conversion_pipeline = spec.conversion.pipeline
    originals_dir = store.originals_dir(settings.data_dir)
    workspace_dir = store.workspace_dir(settings.data_dir)

    safe_path = Path(safe)
    target_stem = safe_path.stem
    parent = str(safe_path.parent)
    search_dir = originals_dir / parent if parent != "." else originals_dir

    original_path: Path | None = None
    if search_dir.exists():
        for candidate in search_dir.iterdir():
            if candidate.is_file() and candidate.stem == target_stem:
                original_path = candidate
                break

    if not original_path:
        raise HTTPException(
            status_code=404,
            detail=f"No original file found for '{safe}'",
        )

    try:
        converter = get_converter(
            conversion_pipeline,
            filename=original_path.name,
            config=spec.conversion.config,
            llm_options=resolved,
        )
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=exc.errors()) from exc
    except (ImportError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    resolved_conversion = conversion_pipeline
    if conversion_pipeline == ConversionPipeline.AUTO:
        resolved_conversion = resolve_auto_pipeline(original_path.name)

    existing_meta = get_metadata(store, safe)
    if existing_meta and existing_meta.images:
        for image_path in existing_meta.images:
            image_full = workspace_dir / image_path
            if image_full.exists():
                image_full.unlink()
                cleanup_empty_parents(image_full, workspace_dir)

    try:
        result = await converter(original_path)
    except ImportError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Conversion failed: {exc!s}",
        ) from exc

    image_result = _store_conversion_images(result, workspace_dir, safe)
    markdown_content = image_result.markdown
    vision = _resolve_vision_config(resolved)
    try:
        markdown_content = await generate_alt_texts(
            markdown_content,
            image_result.alt_text_images,
            vision,
        )
    except Exception:
        logger.warning("Alt text generation failed for %s", safe)

    converted_path = workspace_dir / safe
    converted_path.parent.mkdir(parents=True, exist_ok=True)
    converted_path.write_text(markdown_content, encoding="utf-8")
    stat = converted_path.stat()

    chunk_count = None
    chunking_used = None
    try:
        chunked = await chunk_document(
            store,
            safe,
            markdown_content,
            spec.chunking,
            images=image_result.workspace_paths,
        )
        chunk_count = len(chunked.chunks)
        chunking_used = chunked.pipeline
    except Exception as exc:
        logger.warning("Chunking failed for %s: %s", safe, exc)

    return UploadDocumentResponse(
        filename=original_path.name,
        converted_filename=safe,
        size_bytes=stat.st_size,
        conversion_pipeline_used=resolved_conversion.value,
        chunk_count=chunk_count,
        chunking_pipeline_used=chunking_used,
        message="Document reconverted successfully",
    )


async def upload_file_stream(
    store: Casebase,
    filepath: str,
    content: bytes,
    spec: PipelineSpec,
    llm_config: LlmConfig,
) -> AsyncGenerator[BaseModel, None]:
    """Upload a single file with SSE stage events for progress."""
    basename = filepath.rsplit("/", 1)[-1] if "/" in filepath else filepath
    suffix = "." + basename.rsplit(".", 1)[-1].lower() if "." in basename else ""
    workspace_dir = store.workspace_dir(settings.data_dir)

    # Markdown files: chunk only
    if _is_markdown(suffix):
        yield OperationStageEvent(stage="Chunking document")
        try:
            result = await upload_file(store, filepath, content, spec, llm_config)
            yield UploadCompleteEvent(**result.model_dump())
        except Exception as exc:
            yield OperationErrorEvent(detail=str(exc))
        return

    # Image files: generate alt text + chunk
    if _is_image(suffix):
        vision = _resolve_vision_config(llm_config)
        if vision:
            yield OperationStageEvent(stage="Generating alt text")
        else:
            yield OperationStageEvent(stage="Uploading image")
        try:
            result = await upload_file(store, filepath, content, spec, llm_config)
            yield UploadCompleteEvent(**result.model_dump())
        except Exception as exc:
            yield OperationErrorEvent(detail=str(exc))
        return

    # Binary files: convert -> extract images -> alt text -> chunk
    try:
        yield OperationStageEvent(stage="Converting document")

        originals_dir = store.originals_dir(settings.data_dir)
        original_path = originals_dir / filepath
        original_path.parent.mkdir(parents=True, exist_ok=True)
        original_path.write_bytes(content)

        conversion_pipeline = spec.conversion.pipeline
        try:
            converter = get_converter(
                conversion_pipeline,
                filename=basename,
                config=spec.conversion.config,
                llm_options=llm_config,
            )
        except (ValidationError, ImportError, ValueError) as exc:
            yield OperationErrorEvent(detail=str(exc))
            return

        resolved_conversion = conversion_pipeline
        if conversion_pipeline == ConversionPipeline.AUTO:
            resolved_conversion = resolve_auto_pipeline(basename)

        try:
            conv_result = await converter(original_path)
        except Exception as exc:
            yield OperationErrorEvent(detail=f"Conversion failed: {exc!s}")
            return

        base_name = basename.rsplit(".", 1)[0]
        if "/" in filepath:
            parent_dir = filepath.rsplit("/", 1)[0]
            converted_relpath = f"{parent_dir}/{base_name}.md"
        else:
            converted_relpath = f"{base_name}.md"

        image_result = _store_conversion_images(
            conv_result, workspace_dir, converted_relpath
        )
        markdown_content = image_result.markdown

        if image_result.alt_text_images:
            yield OperationStageEvent(stage="Extracting images")
            vision = _resolve_vision_config(llm_config)
            if vision:
                yield OperationStageEvent(stage="Generating alt text")
            try:
                markdown_content = await generate_alt_texts(
                    markdown_content,
                    image_result.alt_text_images,
                    vision,
                )
            except Exception:
                logger.warning("Alt text generation failed for %s", converted_relpath)

        converted_path = workspace_dir / converted_relpath
        converted_path.parent.mkdir(parents=True, exist_ok=True)
        converted_path.write_text(markdown_content, encoding="utf-8")

        yield OperationStageEvent(stage="Chunking document")
        chunk_count = None
        chunking_used = None
        try:
            chunked = await chunk_document(
                store,
                converted_relpath,
                markdown_content,
                spec.chunking,
                images=image_result.workspace_paths,
            )
            chunk_count = len(chunked.chunks)
            chunking_used = chunked.pipeline
            mark_dirty_and_sync(store)
        except Exception as exc:
            logger.warning("Chunking failed for %s: %s", converted_relpath, exc)

        yield UploadCompleteEvent(
            filename=filepath,
            converted_filename=converted_relpath,
            size_bytes=len(content),
            conversion_pipeline_used=resolved_conversion.value,
            chunk_count=chunk_count,
            chunking_pipeline_used=chunking_used,
            message="Document uploaded and converted successfully",
        )
    except Exception as exc:
        yield OperationErrorEvent(detail=str(exc))


async def reconvert_single_stream(
    store: Casebase,
    safe: str,
    spec: PipelineSpec,
    resolved: LlmConfig,
) -> AsyncGenerator[BaseModel, None]:
    """Reconvert a single document with SSE stage events for progress."""
    try:
        yield OperationStageEvent(stage="Converting document")

        conversion_pipeline = spec.conversion.pipeline
        workspace_dir = store.workspace_dir(settings.data_dir)

        try:
            original_path = find_original(store, safe)
        except HTTPException as exc:
            yield OperationErrorEvent(detail=exc.detail)
            return

        try:
            converter = get_converter(
                conversion_pipeline,
                filename=original_path.name,
                config=spec.conversion.config,
                llm_options=resolved,
            )
        except (ValidationError, ImportError, ValueError) as exc:
            yield OperationErrorEvent(detail=str(exc))
            return

        resolved_conversion = conversion_pipeline
        if conversion_pipeline == ConversionPipeline.AUTO:
            resolved_conversion = resolve_auto_pipeline(original_path.name)

        existing_meta = get_metadata(store, safe)
        if existing_meta and existing_meta.images:
            for image_path in existing_meta.images:
                image_full = workspace_dir / image_path
                if image_full.exists():
                    image_full.unlink()
                    cleanup_empty_parents(image_full, workspace_dir)

        try:
            conv_result = await converter(original_path)
        except Exception as exc:
            yield OperationErrorEvent(detail=f"Conversion failed: {exc!s}")
            return

        image_result = _store_conversion_images(conv_result, workspace_dir, safe)
        markdown_content = image_result.markdown

        if image_result.alt_text_images:
            yield OperationStageEvent(stage="Extracting images")
            vision = _resolve_vision_config(resolved)
            if vision:
                yield OperationStageEvent(stage="Generating alt text")
            try:
                markdown_content = await generate_alt_texts(
                    markdown_content,
                    image_result.alt_text_images,
                    vision,
                )
            except Exception:
                logger.warning("Alt text generation failed for %s", safe)

        converted_path = workspace_dir / safe
        converted_path.parent.mkdir(parents=True, exist_ok=True)
        converted_path.write_text(markdown_content, encoding="utf-8")
        stat = converted_path.stat()

        yield OperationStageEvent(stage="Chunking document")
        chunk_count = None
        chunking_used = None
        try:
            chunked = await chunk_document(
                store,
                safe,
                markdown_content,
                spec.chunking,
                images=image_result.workspace_paths,
            )
            chunk_count = len(chunked.chunks)
            chunking_used = chunked.pipeline
        except Exception as exc:
            logger.warning("Chunking failed for %s: %s", safe, exc)

        yield UploadCompleteEvent(
            filename=original_path.name,
            converted_filename=safe,
            size_bytes=stat.st_size,
            conversion_pipeline_used=resolved_conversion.value,
            chunk_count=chunk_count,
            chunking_pipeline_used=chunking_used,
            message="Document reconverted successfully",
        )
    except Exception as exc:
        yield OperationErrorEvent(detail=str(exc))
