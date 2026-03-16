"""Collection upload helpers for document operations."""

import io
import logging
import tempfile
import zipfile
from collections.abc import AsyncGenerator
from pathlib import Path, PurePosixPath

from fastapi import HTTPException, UploadFile
from starlette.responses import StreamingResponse

from ...chunks import chunk_document
from ...config import sanitize_document_path, settings
from ...converters.alt_text import MD_IMAGE_RE, generate_alt_texts
from ...converters.base import DOCUMENT_EXTENSION
from ...retrieval import mark_dirty_and_sync
from ...store import Casebase
from ...types import CollectionCompleteEvent, CollectionProgressEvent, LlmConfig
from ...converters.wikilinks import preprocess_markdown
from ..common import parse_pipeline_spec, resolve_llm_config
from ..models import PipelineSpec
from .streaming import sse_stream_response
from .uploads import _resolve_vision_config, upload_file

__all__ = [
    "collection_stream_response",
    "process_collection",
    "read_collection_zip",
    "validate_collection_upload",
]

logger = logging.getLogger(__name__)

_MAX_COLLECTION_SIZE_BYTES = 100 * 1024 * 1024
_MAX_COLLECTION_FILES = 1000


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

    async def _try_upload(relative_path: str, content_bytes: bytes) -> bool:
        try:
            safe = sanitize_document_path(relative_path)
            await upload_file(store, safe, content_bytes, spec, resolved)
            return True
        except Exception as exc:
            logger.warning("Failed to process %s: %s", relative_path, exc)
            failed.append(relative_path)
            return False

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
                archive.extractall(extract_root)
        except zipfile.BadZipFile as exc:
            raise HTTPException(status_code=400, detail="Invalid ZIP file") from exc

        top_items = list(extract_root.iterdir())
        if len(top_items) == 1 and top_items[0].is_dir():
            extract_root = top_items[0]

        collection_files = {
            str(path.relative_to(extract_root).as_posix())
            for path in extract_root.rglob("*")
            if path.is_file()
        }
        if len(collection_files) > _MAX_COLLECTION_FILES:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Collection has too many files ({len(collection_files)}). "
                    f"Maximum: {_MAX_COLLECTION_FILES}"
                ),
            )

        all_binaries: set[str] = set()
        all_images: set[str] = set()
        preprocessed: dict[str, str] = {}
        for relative_path in sorted(collection_files):
            if PurePosixPath(relative_path).suffix.lower() != ".md":
                continue
            try:
                text = (extract_root / relative_path).read_text(encoding="utf-8")
            except Exception as exc:
                logger.warning("Failed to read %s: %s", relative_path, exc)
                failed.append(relative_path)
                continue
            result = preprocess_markdown(text, relative_path, collection_files)
            preprocessed[relative_path] = result.content
            all_binaries.update(result.binary_attachments)
            all_images.update(result.image_attachments)

        upload_plan: list[tuple[str, str]] = []
        for path in sorted(all_binaries):
            source = extract_root / path
            if not source.exists():
                failed.append(path)
                continue
            upload_plan.append((path, "binary"))

        for path in sorted(all_images):
            source = extract_root / path
            if not source.exists():
                failed.append(path)
                continue
            upload_plan.append((path, "image"))

        for relative_path in sorted(collection_files):
            if relative_path in all_binaries or relative_path in all_images:
                continue
            suffix = PurePosixPath(relative_path).suffix.lower()
            if suffix == DOCUMENT_EXTENSION and relative_path in preprocessed:
                upload_plan.append((relative_path, "markdown"))
            elif relative_path not in preprocessed:
                upload_plan.append((relative_path, "binary"))

        total = len(upload_plan)
        workspace_dir = store.workspace_dir(settings.data_dir)
        image_count = 0

        for relative_path, category in upload_plan:
            ok = True
            if category == "image":
                try:
                    safe = sanitize_document_path(relative_path)
                    image_destination = workspace_dir / safe
                    image_destination.parent.mkdir(parents=True, exist_ok=True)
                    image_destination.write_bytes(
                        (extract_root / relative_path).read_bytes()
                    )
                    image_count += 1
                except Exception as exc:
                    logger.warning("Failed to store image %s: %s", relative_path, exc)
                    failed.append(relative_path)
                    ok = False
            elif category == "markdown":
                content_bytes = preprocessed[relative_path].encode("utf-8")
                ok = await _try_upload(relative_path, content_bytes)
                if ok:
                    markdown_count += 1
            else:
                content_bytes = (extract_root / relative_path).read_bytes()
                ok = await _try_upload(relative_path, content_bytes)
                if ok:
                    converted_count += 1

            current += 1
            yield CollectionProgressEvent(
                file=relative_path,
                current=current,
                total=total,
                status="ok" if ok else "failed",
            )

        vision = _resolve_vision_config(resolved)
        for relative_path in sorted(preprocessed):
            try:
                safe = sanitize_document_path(relative_path)
            except ValueError:
                continue

            workspace_markdown_path = workspace_dir / safe
            if not workspace_markdown_path.exists():
                continue

            markdown_content = workspace_markdown_path.read_text(encoding="utf-8")
            image_references: dict[str, bytes] = {}
            for match in MD_IMAGE_RE.finditer(markdown_content):
                alt_text, image_path = match.group(1), match.group(2)
                if alt_text or image_path.startswith(("http://", "https://", "data:")):
                    continue
                markdown_dir = PurePosixPath(relative_path).parent
                resolved_image = (
                    str((markdown_dir / image_path).as_posix())
                    if not image_path.startswith("/")
                    else image_path
                )
                image_workspace_path = workspace_dir / resolved_image
                if image_workspace_path.exists() and image_workspace_path.is_file():
                    image_references[image_path] = image_workspace_path.read_bytes()

            if not image_references:
                continue

            try:
                new_content = await generate_alt_texts(
                    markdown_content,
                    image_references,
                    vision,
                )
                if new_content != markdown_content:
                    workspace_markdown_path.write_text(new_content, encoding="utf-8")
                    image_paths = [
                        str((PurePosixPath(relative_path).parent / path).as_posix())
                        for path in image_references
                    ]
                    await chunk_document(
                        store,
                        safe,
                        new_content,
                        spec.chunking,
                        images=image_paths,
                    )
            except Exception:
                logger.warning("Alt text generation failed for %s", relative_path)

        mark_dirty_and_sync(store)

    total_ok = markdown_count + converted_count + image_count
    yield CollectionCompleteEvent(
        total_files=total_ok,
        markdown_files=markdown_count,
        converted_attachments=converted_count,
        failed_files=failed,
        message=(
            f"Collection uploaded: {markdown_count} markdown, "
            f"{converted_count} attachments converted, "
            f"{image_count} images stored"
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


def collection_stream_response(
    store: Casebase,
    raw: bytes,
    spec: PipelineSpec,
    resolved: LlmConfig,
) -> StreamingResponse:
    """Wrap ``process_collection`` in a streaming response."""
    return sse_stream_response(process_collection(store, raw, spec, resolved))


async def read_collection_zip(file: UploadFile) -> bytes:
    """Read and validate a collection ZIP upload."""
    raw = await file.read()
    if len(raw) > _MAX_COLLECTION_SIZE_BYTES:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Collection too large. "
                f"Maximum size: {_MAX_COLLECTION_SIZE_BYTES} bytes"
            ),
        )
    return raw
