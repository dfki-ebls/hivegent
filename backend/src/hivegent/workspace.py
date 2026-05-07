"""Single mutation gateway for casebase workspaces.

Every operation that modifies the workspace, metadata, or search index for
a :class:`~hivegent.store.Casebase` goes through this module.  Each public
function:

- Acquires the per-store async lock so concurrent mutations against the
  same casebase are serialised.
- Performs the workspace and metadata writes in one self-contained step.
- Marks the search index dirty before releasing the lock so the index
  reflects the new state.

Routes, agents, and MCP tools never touch the workspace, metadata, or
LanceDB directories directly — they call into this module instead.  This
collapses the previous "remember to mark dirty" discipline into a single
invariant enforced by the gateway.
"""

from __future__ import annotations

import asyncio
import io
import logging
import mimetypes
import shutil
import threading
import tempfile
import zipfile
import zlib
from collections.abc import AsyncGenerator
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from fastapi import HTTPException
from pydantic import ValidationError

from .chunkers.base import (
    DocumentMetadata,
    EntryGeneratedBy,
    EntryKind,
    EntryMetadata,
    EntryOrigin,
)
from .chunks import chunk_document, delete_metadata, get_metadata
from .config import sanitize_document_path, settings
from .converters import (
    ConversionPipeline,
    get_converter,
    resolve_auto_pipeline,
)
from .converters.alt_text import describe_image
from .converters.base import DOCUMENT_EXTENSION, is_image_suffix, is_markdown_suffix
from .converters.images import guess_image_media_type, sanitize_image_bytes
from .converters.wikilinks import preprocess_markdown
from .entries import (
    assets_dir_for_stem,
    cleanup_empty_parents,
    description_path_for_stem,
    entry_exists,
    metadata_path_for_reference,
    resolve_entry_paths,
    stem_path_from_reference,
)
from .retrieval import invalidate_store, mark_dirty_and_sync
from .store import Casebase
from .types import (
    AssetEntry,
    CollectionCompleteEvent,
    CollectionProgressEvent,
    LlmConfig,
    MoveDirectoryResponse,
    MoveDocumentResponse,
    PipelineSpec,
    UploadCompleteEvent,
    resolve_llm_config,
)

__all__ = [
    "PathReplacement",
    "create_directory",
    "delete_all",
    "delete_directory",
    "delete_document",
    "move_directory",
    "move_document",
    "on_agent_write",
    "process_collection",
    "rechunk",
    "reconvert",
    "replace_original",
    "update_asset_description",
    "upload",
]

logger = logging.getLogger(__name__)


# Per-store async locks.  Created lazily; never removed because they are
# tiny and reusing the same Lock instance across the lifetime of a store
# is a feature.  ``threading.Lock`` guards the dict because asyncio.Lock
# instances bind to the event loop on first acquisition and the dict is
# also touched from synchronous teardown paths.
_locks: dict[str, asyncio.Lock] = {}
_locks_guard = threading.Lock()


def _store_lock(store: Casebase) -> asyncio.Lock:
    """Return the asyncio lock guarding mutations on *store*."""
    key = store.store_key
    with _locks_guard:
        lock = _locks.get(key)
        if lock is None:
            lock = asyncio.Lock()
            _locks[key] = lock
    return lock


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
    """Build the canonical metadata for a logical entry."""
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
    """Write a binary original file into the workspace."""
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
    """Write markdown content and persist its chunk metadata."""
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
    media_type: str,
    llm: LlmConfig,
) -> str:
    """Generate markdown text describing an image, with fallback."""
    aux = resolve_llm_config(llm, default_model=settings.llm.aux_model)
    fallback = PurePosixPath(filepath).stem
    if not aux.model or not media_type:
        return f"{fallback}\n"
    try:
        description = await describe_image(content, media_type, aux)
    except Exception:
        logger.warning(
            "Image description generation failed for %s", filepath, exc_info=True
        )
        description = fallback
    return f"{description.strip() or fallback}\n"


def _build_binary_stub(filepath: str, size_bytes: int) -> str:
    """Build a searchable markdown stub for a non-convertible binary."""
    name = PurePosixPath(filepath).name
    mime = mimetypes.guess_type(filepath)[0] or "application/octet-stream"
    return f"File name: {name}.\nMIME type: {mime}.\nSize: {size_bytes} bytes.\n"


def _clear_assets_subtree(store: Casebase, stem_path: str) -> None:
    """Delete a logical entry's child-assets subtree from disk."""
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


# ---------------------------------------------------------------------------
# Locked upload primitives
# ---------------------------------------------------------------------------


async def _upload_markdown_locked(
    store: Casebase,
    filepath: str,
    content: bytes,
    spec: PipelineSpec,
    *,
    origin: EntryOrigin,
) -> UploadCompleteEvent:
    """Persist a user-authored markdown document. Caller holds the lock."""
    workspace_dir = store.workspace_dir(settings.data_dir)
    text = content.decode("utf-8")
    stem_path = stem_path_from_reference(filepath)
    assets_dir = assets_dir_for_stem(stem_path)
    chunk_count, chunking_used = await _write_markdown_projection(
        store,
        filepath,
        text,
        spec,
        entry_metadata=_build_entry_metadata(
            stem_path=stem_path,
            description_path=filepath,
            original_path=None,
            assets_dir=assets_dir
            if (workspace_dir / assets_dir).exists()
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


async def _upload_image_locked(
    store: Casebase,
    filepath: str,
    content: bytes,
    spec: PipelineSpec,
    llm: LlmConfig,
    *,
    origin: EntryOrigin,
) -> UploadCompleteEvent:
    """Persist an image and its generated description. Caller holds the lock."""
    workspace_dir = store.workspace_dir(settings.data_dir)
    media_type = guess_image_media_type(filepath) or ""
    sanitized = sanitize_image_bytes(content, media_type)
    _write_original_file(workspace_dir, filepath, sanitized)
    stem_path = stem_path_from_reference(filepath)
    description_path = description_path_for_stem(stem_path)
    markdown = await _build_image_description(filepath, sanitized, media_type, llm)
    chunk_count, chunking_used = await _write_markdown_projection(
        store,
        description_path,
        markdown,
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


async def _upload_binary_stub_locked(
    store: Casebase,
    filepath: str,
    content: bytes,
    spec: PipelineSpec,
    *,
    origin: EntryOrigin,
    original_written: bool = False,
) -> UploadCompleteEvent:
    """Persist a non-convertible binary with a searchable stub."""
    workspace_dir = store.workspace_dir(settings.data_dir)
    if not original_written:
        _write_original_file(workspace_dir, filepath, content)
    stem_path = stem_path_from_reference(filepath)
    description_path = description_path_for_stem(stem_path)
    markdown = _build_binary_stub(filepath, len(content))
    chunk_count, chunking_used = await _write_markdown_projection(
        store,
        description_path,
        markdown,
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


async def _upload_convertible_locked(
    store: Casebase,
    filepath: str,
    content: bytes,
    spec: PipelineSpec,
    llm: LlmConfig,
    *,
    origin: EntryOrigin,
) -> UploadCompleteEvent:
    """Convert a binary, persist its markdown, and process extracted assets."""
    workspace_dir = store.workspace_dir(settings.data_dir)
    original_full_path = _write_original_file(workspace_dir, filepath, content)
    basename = PurePosixPath(filepath).name
    conversion_pipeline = spec.conversion.pipeline

    try:
        converter = get_converter(
            conversion_pipeline,
            filename=basename,
            config=spec.conversion.config,
            llm_options=llm,
        )
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=exc.errors()) from exc
    except (ImportError, ValueError) as exc:
        if conversion_pipeline == ConversionPipeline.AUTO:
            return await _upload_binary_stub_locked(
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
            return await _upload_binary_stub_locked(
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
    markdown = result.markdown

    child_paths: list[tuple[str, str, bytes]] = []
    for image_relpath, image_data in sorted(result.images.items()):
        image_media_type = guess_image_media_type(image_relpath) or ""
        child_path = str((PurePosixPath(assets_dir) / image_relpath).as_posix())
        relative_from_doc = str(
            PurePosixPath(PurePosixPath(assets_dir).name) / PurePosixPath(image_relpath)
        )
        markdown = markdown.replace(image_relpath, relative_from_doc)
        child_paths.append((child_path, image_media_type, image_data))

    if child_paths and spec.process_assets:
        await asyncio.gather(
            *(
                _upload_locked(
                    store,
                    child_path,
                    image_data,
                    spec,
                    llm,
                    origin="extracted",
                )
                for child_path, _, image_data in child_paths
            )
        )
    elif child_paths:
        for child_path, image_media_type, image_data in child_paths:
            _write_original_file(
                workspace_dir,
                child_path,
                sanitize_image_bytes(image_data, image_media_type),
            )

    chunk_count, chunking_used = await _write_markdown_projection(
        store,
        description_path,
        markdown,
        spec,
        entry_metadata=_build_entry_metadata(
            stem_path=stem_path,
            description_path=description_path,
            original_path=filepath,
            assets_dir=assets_dir if child_paths else None,
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


async def _upload_locked(
    store: Casebase,
    filepath: str,
    content: bytes,
    spec: PipelineSpec,
    llm: LlmConfig,
    *,
    origin: EntryOrigin,
) -> UploadCompleteEvent:
    """Dispatch to the per-kind upload handler. Caller holds the lock."""
    suffix = PurePosixPath(filepath).suffix.lower()
    if is_markdown_suffix(suffix):
        return await _upload_markdown_locked(
            store, filepath, content, spec, origin=origin
        )
    if is_image_suffix(suffix):
        return await _upload_image_locked(
            store, filepath, content, spec, llm, origin=origin
        )
    return await _upload_convertible_locked(
        store, filepath, content, spec, llm, origin=origin
    )


def _delete_single_locked(store: Casebase, safe: str) -> None:
    """Remove a logical entry's files and metadata. Caller holds the lock."""
    workspace = store.workspace_dir(settings.data_dir)
    metadata_dir = store.metadata_dir(settings.data_dir)
    metadata = get_metadata(store, safe)
    if not metadata:
        description_path = workspace / safe
        if not description_path.exists():
            raise HTTPException(status_code=404, detail="Document not found")
        if not description_path.is_file():
            raise HTTPException(status_code=400, detail="Path is not a file")

    resolved = resolve_entry_paths(workspace, safe)
    description_rel = (
        metadata.description_path
        if metadata and metadata.description_path
        else resolved.description_path
    )
    description_path = workspace / description_rel
    if description_path.exists():
        description_path.unlink()
        cleanup_empty_parents(description_path, workspace)

    original_rel = metadata.original_path if metadata else resolved.original_path
    if original_rel:
        original_path = workspace / original_rel
        if original_path.exists():
            original_path.unlink()
            cleanup_empty_parents(original_path, workspace)

    assets_rel = metadata.assets_dir if metadata else resolved.assets_dir
    if assets_rel:
        assets_path = workspace / assets_rel
        if assets_path.exists():
            shutil.rmtree(assets_path)
            cleanup_empty_parents(assets_path, workspace)
        metadata_assets = metadata_dir / assets_rel
        if metadata_assets.exists():
            shutil.rmtree(metadata_assets)
            cleanup_empty_parents(metadata_assets, metadata_dir)

    delete_metadata(store, safe)


def _ensure_upload_slot_locked(
    store: Casebase, reference: str, *, overwrite: bool
) -> None:
    """Free up an upload slot for *reference*, raising 409 if occupied."""
    workspace_dir = store.workspace_dir(settings.data_dir)
    metadata_dir = store.metadata_dir(settings.data_dir)
    if not entry_exists(workspace_dir, metadata_dir, reference):
        return
    if not overwrite:
        raise HTTPException(status_code=409, detail="Document already exists")
    _delete_single_locked(
        store, description_path_for_stem(stem_path_from_reference(reference))
    )


# ---------------------------------------------------------------------------
# Path-rewriting helpers for moves
# ---------------------------------------------------------------------------


@dataclass(slots=True, frozen=True)
class PathReplacement:
    """Prefix replacement applied to metadata path fields during moves."""

    old: str
    new: str


def _replace_path_value(
    value: str | None, replacements: list[PathReplacement]
) -> str | None:
    """Apply each replacement to a path-typed metadata field."""
    if value is None:
        return None
    updated = value
    for replacement in replacements:
        if updated == replacement.old or updated.startswith(f"{replacement.old}/"):
            updated = replacement.new + updated[len(replacement.old) :]
    return updated


def _rewrite_metadata_paths(
    meta_path: Path, replacements: list[PathReplacement]
) -> None:
    """Rewrite path fields inside a metadata JSON file in place."""
    try:
        metadata = DocumentMetadata.model_validate_json(
            meta_path.read_text(encoding="utf-8")
        )
    except Exception:
        return
    updated = metadata.model_copy(
        update={
            "stem_path": _replace_path_value(metadata.stem_path, replacements)
            or metadata.stem_path,
            "description_path": _replace_path_value(
                metadata.description_path, replacements
            )
            or metadata.description_path,
            "original_path": _replace_path_value(metadata.original_path, replacements),
            "assets_dir": _replace_path_value(metadata.assets_dir, replacements),
            "files": [
                replaced
                for f in metadata.files
                if (replaced := _replace_path_value(f, replacements)) is not None
            ],
        }
    )
    meta_path.write_text(
        updated.model_dump_json(indent=2, exclude_none=True),
        encoding="utf-8",
    )


def _rewrite_metadata_tree(
    metadata_root: Path, replacements: list[PathReplacement]
) -> None:
    """Rewrite path fields in every metadata JSON beneath a subtree."""
    if metadata_root.is_file():
        _rewrite_metadata_paths(metadata_root, replacements)
        return
    for meta_file in metadata_root.rglob("*.json"):
        _rewrite_metadata_paths(meta_file, replacements)


# ---------------------------------------------------------------------------
# Public mutation API
# ---------------------------------------------------------------------------


async def upload(
    store: Casebase,
    filepath: str,
    content: bytes,
    *,
    spec: PipelineSpec | None = None,
    llm: LlmConfig | None = None,
    origin: EntryOrigin = "upload",
    overwrite: bool = False,
) -> UploadCompleteEvent:
    """Upload a document to *store*, sanitising and chunking as needed.

    The casebase lock is held for the entire operation, including
    extracted-asset child uploads.  The search index is marked dirty
    before the lock is released.
    """
    spec = spec or PipelineSpec()
    llm = llm or LlmConfig()
    async with _store_lock(store):
        try:
            _ensure_upload_slot_locked(store, filepath, overwrite=overwrite)
            return await _upload_locked(
                store, filepath, content, spec, llm, origin=origin
            )
        finally:
            mark_dirty_and_sync(store)


async def replace_original(
    store: Casebase,
    safe: str,
    new_content: bytes,
    *,
    new_filename: str | None,
    spec: PipelineSpec | None = None,
    llm: LlmConfig | None = None,
) -> UploadCompleteEvent:
    """Replace the original file backing a logical entry and reconvert.

    The new original keeps the entry's stem; only the suffix may change.
    """
    spec = spec or PipelineSpec()
    llm = llm or LlmConfig()
    async with _store_lock(store):
        try:
            metadata = get_metadata(store, safe)
            workspace_dir = store.workspace_dir(settings.data_dir)
            existing_original_rel = (
                metadata.original_path
                if metadata
                else resolve_entry_paths(workspace_dir, safe).original_path
            )
            if not existing_original_rel:
                raise HTTPException(
                    status_code=404,
                    detail=f"No original file found for '{safe}'",
                )
            existing_original_path = workspace_dir / existing_original_rel

            new_suffix = (
                PurePosixPath(new_filename).suffix
                if new_filename
                else existing_original_path.suffix
            ) or existing_original_path.suffix
            new_original_relpath = (
                f"{stem_path_from_reference(safe)}{new_suffix.lower()}"
            )

            if existing_original_rel != new_original_relpath:
                existing_original_path.unlink(missing_ok=True)

            origin = metadata.origin if metadata else "upload"
            _clear_assets_subtree(
                store,
                metadata.stem_path
                if metadata
                else stem_path_from_reference(safe),
            )
            return await _upload_locked(
                store,
                new_original_relpath,
                new_content,
                spec,
                llm,
                origin=origin,
            )
        finally:
            mark_dirty_and_sync(store)


async def reconvert(
    store: Casebase,
    safe: str,
    *,
    spec: PipelineSpec | None = None,
    llm: LlmConfig | None = None,
    sync: bool = True,
) -> UploadCompleteEvent:
    """Re-run conversion and chunking for an entry's existing original.

    Pass ``sync=False`` when invoked inside a bulk loop; the caller is
    then responsible for a single :func:`mark_dirty_and_sync` after the
    loop so the index rebuild is coalesced into one pass.
    """
    spec = spec or PipelineSpec()
    llm = llm or LlmConfig()
    async with _store_lock(store):
        try:
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
            return await _upload_locked(
                store,
                metadata.original_path,
                original_path.read_bytes(),
                spec,
                llm,
                origin=metadata.origin,
            )
        finally:
            if sync:
                mark_dirty_and_sync(store)


async def rechunk(
    store: Casebase,
    safe: str,
    *,
    spec: PipelineSpec | None = None,
    sync: bool = True,
) -> DocumentMetadata:
    """Re-chunk an existing markdown document.

    Pass ``sync=False`` when invoked inside a bulk loop; see
    :func:`reconvert` for the caller's coalescing responsibility.
    """
    spec = spec or PipelineSpec()
    async with _store_lock(store):
        try:
            workspace_dir = store.workspace_dir(settings.data_dir)
            file_path = workspace_dir / safe
            if not file_path.exists() or not file_path.is_file():
                raise HTTPException(status_code=404, detail="Document not found")
            text = file_path.read_text(encoding="utf-8")
            return await chunk_document(store, safe, text, spec.chunking)
        finally:
            if sync:
                mark_dirty_and_sync(store)


async def delete_document(store: Casebase, safe: str, *, sync: bool = True) -> None:
    """Delete a logical entry and all of its files.

    Pass ``sync=False`` when invoked inside a bulk loop; see
    :func:`reconvert` for the caller's coalescing responsibility.
    """
    async with _store_lock(store):
        try:
            _delete_single_locked(store, safe)
        finally:
            if sync:
                mark_dirty_and_sync(store)


async def move_document(
    store: Casebase, src: str, dst: str
) -> MoveDocumentResponse:
    """Move a logical entry, its original, and its child-assets subtree."""
    async with _store_lock(store):
        try:
            workspace_dir = store.workspace_dir(settings.data_dir)
            metadata_dir = store.metadata_dir(settings.data_dir)

            metadata = get_metadata(store, src)
            if not metadata:
                raise HTTPException(status_code=404, detail="Document not found")

            src_stem = metadata.stem_path or stem_path_from_reference(src)
            dst_stem = stem_path_from_reference(dst)
            if src_stem != dst_stem and entry_exists(
                workspace_dir, metadata_dir, dst
            ):
                raise HTTPException(
                    status_code=409, detail="Destination already exists"
                )

            src_description = metadata.description_path or description_path_for_stem(
                src_stem
            )
            dst_description = description_path_for_stem(dst_stem)
            src_description_path = workspace_dir / src_description
            if not src_description_path.exists():
                raise HTTPException(status_code=404, detail="Document not found")

            dst_description_path = workspace_dir / dst_description
            dst_description_path.parent.mkdir(parents=True, exist_ok=True)
            src_description_path.rename(dst_description_path)
            cleanup_empty_parents(src_description_path, workspace_dir)

            dst_original: str | None = None
            if metadata.original_path:
                src_original_path = workspace_dir / metadata.original_path
                if src_original_path.exists():
                    dst_original = f"{dst_stem}{src_original_path.suffix}"
                    dst_original_path = workspace_dir / dst_original
                    dst_original_path.parent.mkdir(parents=True, exist_ok=True)
                    src_original_path.rename(dst_original_path)
                    cleanup_empty_parents(src_original_path, workspace_dir)

            dst_assets_dir: str | None = None
            src_assets_dir = metadata.assets_dir
            if src_assets_dir:
                src_assets_path = workspace_dir / src_assets_dir
                if src_assets_path.exists():
                    dst_assets_dir = assets_dir_for_stem(dst_stem)
                    dst_assets_path = workspace_dir / dst_assets_dir
                    dst_assets_path.parent.mkdir(parents=True, exist_ok=True)
                    src_assets_path.rename(dst_assets_path)
                    cleanup_empty_parents(src_assets_path, workspace_dir)

                    src_assets_name = PurePosixPath(src_assets_dir).name
                    dst_assets_name = PurePosixPath(dst_assets_dir).name
                    if src_assets_name != dst_assets_name:
                        body = dst_description_path.read_text(encoding="utf-8")
                        body = body.replace(
                            f"{src_assets_name}/", f"{dst_assets_name}/"
                        )
                        dst_description_path.write_text(body, encoding="utf-8")

            src_meta_path = metadata_path_for_reference(store, src_stem)
            dst_meta_path = metadata_path_for_reference(store, dst_stem)
            if src_meta_path.exists():
                dst_meta_path.parent.mkdir(parents=True, exist_ok=True)
                src_meta_path.rename(dst_meta_path)
                cleanup_empty_parents(src_meta_path, metadata_dir)

            src_meta_assets = metadata_dir / assets_dir_for_stem(src_stem)
            dst_meta_assets = metadata_dir / assets_dir_for_stem(dst_stem)
            if src_meta_assets.exists():
                dst_meta_assets.parent.mkdir(parents=True, exist_ok=True)
                src_meta_assets.rename(dst_meta_assets)
                cleanup_empty_parents(src_meta_assets, metadata_dir)

            replacements = [
                PathReplacement(old=src_stem, new=dst_stem),
                PathReplacement(old=src_description, new=dst_description),
            ]
            if metadata.original_path and dst_original:
                replacements.append(
                    PathReplacement(old=metadata.original_path, new=dst_original)
                )
            if src_assets_dir and dst_assets_dir:
                replacements.append(
                    PathReplacement(old=src_assets_dir, new=dst_assets_dir)
                )
            if dst_meta_path.exists():
                _rewrite_metadata_paths(dst_meta_path, replacements)
            if dst_meta_assets.exists():
                _rewrite_metadata_tree(dst_meta_assets, replacements)

            return MoveDocumentResponse(
                source=src,
                destination=dst_description,
                message="Document moved successfully",
            )
        finally:
            mark_dirty_and_sync(store)


async def update_asset_description(
    store: Casebase,
    safe: str,
    asset_name: str,
    content: str,
) -> AssetEntry:
    """Update the companion ``.md`` description of an asset.

    Persists chunk metadata for the description so it is searchable
    immediately after the call returns.
    """
    async with _store_lock(store):
        try:
            workspace = store.workspace_dir(settings.data_dir)
            assets_dir = assets_dir_for_stem(stem_path_from_reference(safe))
            assets_path = workspace / assets_dir
            if not assets_path.exists() or not assets_path.is_dir():
                raise HTTPException(
                    status_code=404, detail="Document has no assets directory"
                )
            asset_path = assets_path / asset_name
            if not asset_path.exists() or not asset_path.is_file():
                raise HTTPException(status_code=404, detail="Asset file not found")

            md_path = asset_path.with_suffix(DOCUMENT_EXTENSION)
            md_path.write_text(content, encoding="utf-8")

            description_path = str(md_path.relative_to(workspace).as_posix())
            rel_path = str(asset_path.relative_to(workspace).as_posix())

            await chunk_document(store, description_path, content)
            return AssetEntry(
                name=asset_name,
                path=rel_path,
                description_path=description_path,
                description=content,
                size_bytes=asset_path.stat().st_size,
                media_type=mimetypes.guess_type(asset_name)[0],
            )
        finally:
            mark_dirty_and_sync(store)


async def create_directory(store: Casebase, path: str) -> None:
    """Create an empty workspace directory."""
    async with _store_lock(store):
        directory_path = store.workspace_dir(settings.data_dir) / path
        if directory_path.exists():
            raise HTTPException(status_code=409, detail="Directory already exists")
        directory_path.mkdir(parents=True, exist_ok=True)


async def move_directory(
    store: Casebase, src: str, dst: str
) -> MoveDirectoryResponse:
    """Move or rename a workspace directory and its metadata subtree."""
    async with _store_lock(store):
        try:
            workspace_dir = store.workspace_dir(settings.data_dir)
            metadata_dir = store.metadata_dir(settings.data_dir)

            src_dir = workspace_dir / src
            if not src_dir.exists() or not src_dir.is_dir():
                raise HTTPException(status_code=404, detail="Directory not found")
            dst_dir = workspace_dir / dst
            if dst_dir.exists():
                raise HTTPException(
                    status_code=409, detail="Destination already exists"
                )

            files_moved = sum(
                1 for file_path in src_dir.rglob("*") if file_path.is_file()
            )
            dst_dir.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(src_dir), str(dst_dir))
            cleanup_empty_parents(src_dir, workspace_dir)

            src_meta = metadata_dir / src
            dst_meta = metadata_dir / dst
            if src_meta.exists():
                dst_meta.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(src_meta), str(dst_meta))
                cleanup_empty_parents(src_meta, metadata_dir)
                _rewrite_metadata_tree(
                    dst_meta, [PathReplacement(old=src, new=dst)]
                )

            return MoveDirectoryResponse(
                source=src,
                destination=dst,
                files_moved=files_moved,
                message="Directory moved successfully",
            )
        finally:
            mark_dirty_and_sync(store)


async def delete_directory(store: Casebase, path: str) -> int:
    """Delete a workspace directory and its metadata subtree."""
    async with _store_lock(store):
        try:
            workspace_dir = store.workspace_dir(settings.data_dir)
            metadata_dir = store.metadata_dir(settings.data_dir)
            directory_path = workspace_dir / path
            if not directory_path.exists() or not directory_path.is_dir():
                raise HTTPException(status_code=404, detail="Directory not found")
            files_deleted = sum(
                1 for file_path in directory_path.rglob("*") if file_path.is_file()
            )
            shutil.rmtree(directory_path)
            cleanup_empty_parents(directory_path, workspace_dir)
            metadata_subdir = metadata_dir / path
            if metadata_subdir.exists():
                shutil.rmtree(metadata_subdir)
                cleanup_empty_parents(metadata_subdir, metadata_dir)
            return files_deleted
        finally:
            mark_dirty_and_sync(store)


async def delete_all(store: Casebase) -> None:
    """Wipe a casebase's workspace, metadata, and search index."""
    async with _store_lock(store):
        await invalidate_store(store)
        data_dir = settings.data_dir
        for directory in (
            store.workspace_path(data_dir),
            store.metadata_path(data_dir),
            store.root_path(data_dir) / "lancedb",
        ):
            if directory.exists():
                shutil.rmtree(directory)


async def on_agent_write(store: Casebase, filename: str) -> None:
    """Re-chunk a document after an agent or MCP write tool modified it."""
    async with _store_lock(store):
        try:
            workspace = store.workspace_dir(settings.data_dir)
            file_path = workspace / filename
            try:
                text = file_path.read_text(encoding="utf-8")
            except OSError:
                logger.warning("Re-chunking failed for %s after write", filename)
                return
            try:
                await chunk_document(store, filename, text)
            except Exception:
                logger.warning(
                    "Re-chunking failed for %s after write", filename, exc_info=True
                )
        finally:
            mark_dirty_and_sync(store)


# ---------------------------------------------------------------------------
# Collection upload
# ---------------------------------------------------------------------------


async def process_collection(
    store: Casebase,
    raw: bytes,
    spec: PipelineSpec,
    llm: LlmConfig,
) -> AsyncGenerator[CollectionProgressEvent | CollectionCompleteEvent, None]:
    """Process a ZIP collection and yield progress events for each file.

    The casebase lock is held for the entire collection so a concurrent
    upload elsewhere cannot interleave.  The index is marked dirty when
    the generator finishes (including on cancellation).
    """
    failed: list[str] = []
    markdown_count = 0
    converted_count = 0
    current = 0

    async with _store_lock(store):
        try:
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
                    raise HTTPException(
                        status_code=400, detail="Invalid ZIP file"
                    ) from exc
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
                companion_originals: set[str] = set()

                for relative_path in collection_files:
                    safe = sanitize_document_path(relative_path)
                    suffix = PurePosixPath(safe).suffix.lower()
                    if suffix == DOCUMENT_EXTENSION:
                        try:
                            text = (extract_root / relative_path).read_text(
                                encoding="utf-8"
                            )
                        except Exception as exc:
                            logger.warning(
                                "Failed to read %s: %s", relative_path, exc
                            )
                            failed.append(relative_path)
                            continue
                        normalized_md = preprocess_markdown(
                            text, safe, frozenset(collection_files)
                        )
                        preprocessed_markdown[safe] = normalized_md.content.encode(
                            "utf-8"
                        )

                    stem = stem_path_from_reference(safe)
                    if entry_exists(workspace_dir, metadata_dir, safe):
                        failed.append(relative_path)
                        continue
                    if stem in collection_stems:
                        if suffix != DOCUMENT_EXTENSION:
                            companion_originals.add(relative_path)
                        else:
                            failed.append(relative_path)
                        continue
                    collection_stems.add(stem)

                total = len(collection_files)
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
                        try:
                            original_bytes = (
                                extract_root / relative_path
                            ).read_bytes()
                            original_path = workspace_dir / safe
                            original_path.parent.mkdir(parents=True, exist_ok=True)
                            original_path.write_bytes(original_bytes)
                            status = "ok"
                        except Exception as exc:
                            logger.warning(
                                "Failed to write original %s: %s",
                                relative_path,
                                exc,
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
                            content_bytes = (
                                extract_root / relative_path
                            ).read_bytes()
                            converted_count += 1
                        await _upload_locked(
                            store,
                            safe,
                            content_bytes,
                            spec,
                            llm,
                            origin="collection",
                        )
                        status = "ok"
                    except Exception as exc:
                        logger.warning(
                            "Failed to process %s: %s", relative_path, exc
                        )
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
