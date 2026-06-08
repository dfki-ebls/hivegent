"""Single mutation gateway for casebase workspaces.

Every operation that modifies the workspace or the SQL documents for a
:class:`~hivegent.store.Casebase` goes through this module.  Each
public function acquires the per-store async lock so concurrent
mutations on the same casebase are serialised, then performs the
workspace and SQL writes in one step — see
:func:`hivegent.chunks.chunk_and_index_document` and
:func:`hivegent.chunks.delete_document`.

Chunks (text + vector) live next to documents in Postgres and cascade
on delete: any operation that drops a Document row also drops its
chunks in the same transaction.  Routes, agents, and MCP tools never
touch the workspace or the database directly — they call into this
module instead.

SQL is the source of truth; workspace files are a derived projection.
Disk-vs-SQL orphans are reconciled at startup by
:mod:`hivegent.reconcile`.
"""

import asyncio
import io
import logging
import mimetypes
import re
import shutil
import stat
import tempfile
import threading
import zipfile
import zlib
from collections.abc import AsyncGenerator, AsyncIterator, Sequence
from contextlib import asynccontextmanager
from pathlib import Path, PurePosixPath

import logfire
from fastapi import HTTPException
from pydantic import ValidationError

from .chunkers.base import (
    DocumentMetadata,
    EntryGeneratedBy,
    EntryKind,
    EntryMetadata,
    EntryOrigin,
)
from .chunks import (
    chunk_and_index_document,
    delete_document as _delete_chunked_document,
)
from .config import sanitize_document_path, settings
from .converters import (
    ConversionPipeline,
    get_converter,
    resolve_auto_pipeline,
)
from .converters.asset_processing import (
    MD_IMAGE_RE,
    TriageDecision,
    caption_image,
    image_context_windows,
    perceptual_key,
    triage_image,
)
from .converters.base import (
    DOCUMENT_EXTENSION,
    ExtractedImage,
    is_external_ref,
    is_image_suffix,
    is_markdown_suffix,
)
from .converters.images import guess_image_media_type, sanitize_image_bytes
from .converters.wikilinks import preprocess_markdown
from .db import documents as db_documents
from .entries import (
    asset_ref_for,
    assets_dir_for_stem,
    cleanup_empty_parents,
    description_path_for_stem,
    entry_exists,
    resolve_entry_paths,
    stem_path_from_reference,
)
from .store import Casebase
from .types import (
    AssetEntry,
    AssetProcessingMode,
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
    "create_directory",
    "delete_all",
    "delete_directory",
    "delete_document",
    "delete_workspace_root",
    "edit_document_text",
    "generate_asset_description",
    "move_directory",
    "move_document",
    "process_collection",
    "rechunk",
    "reconvert",
    "replace_original",
    "store_lock",
    "update_asset_description",
    "upload",
    "write_document_text",
]

logger = logging.getLogger(__name__)


# Per-store async locks.  Created lazily; never removed because they are
# tiny and reusing the same Lock instance across the lifetime of a store
# is a feature.  ``threading.Lock`` guards the dict because asyncio.Lock
# instances bind to the event loop on first acquisition and the dict is
# also touched from synchronous teardown paths.
_locks: dict[str, asyncio.Lock] = {}
_locks_guard = threading.Lock()


def store_lock(store: Casebase) -> asyncio.Lock:
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
    """Write markdown content and persist chunks (with vectors) in one tx.

    The chunk + embed + SQL upsert step is shielded so a cancel cannot
    leave the workspace markdown without its SQL rows.  A partial
    markdown file on disk is caught by the startup reconciler.
    """
    workspace_dir = store.workspace_dir(settings.data_dir)
    full_path = workspace_dir / description_path
    full_path.parent.mkdir(parents=True, exist_ok=True)
    full_path.write_text(content, encoding="utf-8")
    chunked = await asyncio.shield(
        chunk_and_index_document(
            store,
            description_path,
            content,
            spec.chunking,
            entry_metadata=entry_metadata,
        )
    )
    return len(chunked.chunks), chunked.pipeline


async def _build_image_description(
    filepath: str,
    content: bytes,
    media_type: str,
    contexts: Sequence[str],
    llm: LlmConfig,
) -> str:
    """Generate markdown describing an image, grounded in *contexts*, with fallback."""
    aux = resolve_llm_config(llm, default_model=settings.llm.aux_model)
    fallback = PurePosixPath(filepath).stem
    if not aux.model or not media_type:
        return f"{fallback}\n"
    try:
        description = await caption_image(content, media_type, contexts, aux)
    except Exception:
        logger.warning(
            "Image description generation failed for %s", filepath, exc_info=True
        )
        description = fallback
    return f"{description.strip() or fallback}\n"


async def _persist_image_entry(
    store: Casebase,
    workspace_dir: Path,
    filepath: str,
    content: bytes,
    media_type: str,
    contexts: Sequence[str],
    spec: PipelineSpec,
    llm: LlmConfig,
    *,
    origin: EntryOrigin,
) -> tuple[int, str, str]:
    """Write an image plus its context-grounded caption as one vision entry.

    Shared by standalone image uploads and the described assets extracted from
    a converted document; *contexts* carries every occurrence's surrounding
    text so the caption is the single source of truth for that image. Returns
    ``(chunk_count, chunking_pipeline_used, description_path)``.
    """
    _write_original_file(
        workspace_dir, filepath, sanitize_image_bytes(content, media_type)
    )
    stem_path = stem_path_from_reference(filepath)
    description_path = description_path_for_stem(stem_path)
    markdown = await _build_image_description(
        filepath, content, media_type, contexts, llm
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
            assets_dir=None,
            entry_kind="image",
            origin=origin,
            generated_by="vision",
        ),
    )
    return chunk_count, chunking_used, description_path


def _build_binary_stub(filepath: str, size_bytes: int) -> str:
    """Build a searchable markdown stub for a non-convertible binary."""
    name = PurePosixPath(filepath).name
    mime = mimetypes.guess_type(filepath)[0] or "application/octet-stream"
    return f"File name: {name}.\nMIME type: {mime}.\nSize: {size_bytes} bytes.\n"


def _replace_image_references(markdown: str, mapping: dict[str, str | None]) -> str:
    """Rewrite or strip ``![alt](path)`` references in *markdown*.

    Bounded to real markdown image syntax so prose that mentions an
    asset's filename (code blocks, file listings) is left untouched.
    Mapping values: a string replaces the URL; ``None`` deletes the
    image node entirely. References left unmapped are dropped when they
    point outside the workspace (absolute, ``file:``, or Windows paths a
    converter could not localize) and kept otherwise.
    """

    def _replace(match: re.Match[str]) -> str:
        path = match.group(2)
        if path in mapping:
            target = mapping[path]
            return "" if target is None else f"![{match.group(1)}]({target})"
        return "" if is_external_ref(path) else match.group(0)

    return MD_IMAGE_RE.sub(_replace, markdown)


async def _clear_assets_subtree(store: Casebase, stem_path: str) -> None:
    """Delete a logical entry's child-assets directory from the workspace.

    SQL child-document rows that lived under the parent stem are
    dropped by :func:`db_documents.delete_subtree`; chunks cascade with
    them.  This helper handles only the on-disk asset files.
    """
    workspace_dir = store.workspace_dir(settings.data_dir)
    assets_dir = assets_dir_for_stem(stem_path)
    workspace_assets = workspace_dir / assets_dir
    if workspace_assets.exists():
        shutil.rmtree(workspace_assets)
        cleanup_empty_parents(workspace_assets, workspace_dir)
    await db_documents.delete_subtree(store, assets_dir)


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
            assets_dir=assets_dir if (workspace_dir / assets_dir).exists() else None,
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
    chunk_count, chunking_used, description_path = await _persist_image_entry(
        store,
        workspace_dir,
        filepath,
        content,
        media_type,
        [f"File name: {PurePosixPath(filepath).name}"],
        spec,
        llm,
        origin=origin,
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


async def _process_conversion_assets(
    store: Casebase,
    workspace_dir: Path,
    assets_dir: str,
    images: dict[str, ExtractedImage],
    contexts_by_ref: dict[str, list[str]],
    mode: AssetProcessingMode,
    spec: PipelineSpec,
    llm: LlmConfig,
) -> dict[str, str | None]:
    """Persist a conversion's extracted images and return their ref remapping.

    Store-only assets (decorative, or ``STORE`` mode) are written verbatim and
    keep their own reference. Described assets are grouped by
    :func:`perceptual_key`: each group is captioned once from the joint context
    of all its occurrences, persisted as a single representative entry, and
    every occurrence's reference is rewritten to that representative — so an
    image is captioned once and shared, never once per occurrence.
    """

    def child_path(relpath: str) -> str:
        return str((PurePosixPath(assets_dir) / relpath).as_posix())

    ref_mapping: dict[str, str | None] = {}
    # Group described occurrences by perceptual identity so duplicates collapse
    # to one captioned entry. Images with no stable key (uniform or undecodable)
    # get a unique sentinel so each stays its own singleton group.
    groups: dict[object, list[str]] = {}

    for relpath, extracted in sorted(images.items()):
        describe = (
            mode is AssetProcessingMode.DESCRIBE
            and triage_image(extracted) is TriageDecision.DESCRIBE
        )
        if not describe:
            ref_mapping[relpath] = asset_ref_for(assets_dir, relpath)
            _write_original_file(
                workspace_dir,
                child_path(relpath),
                sanitize_image_bytes(
                    extracted.data, guess_image_media_type(relpath) or ""
                ),
            )
            continue
        key = perceptual_key(extracted.data)
        groups.setdefault(key if key is not None else object(), []).append(relpath)

    for members in groups.values():
        rep_ref = asset_ref_for(assets_dir, members[0])
        for member in members:
            ref_mapping[member] = rep_ref

    async def _caption_group(members: list[str]) -> None:
        representative = members[0]
        contexts: list[str] = []
        for member in members:
            contexts.extend(contexts_by_ref.get(member, []))
            if caption := images[member].caption:
                contexts.append(f"Figure caption: {caption}")
        await _persist_image_entry(
            store,
            workspace_dir,
            child_path(representative),
            images[representative].data,
            guess_image_media_type(representative) or "",
            contexts,
            spec,
            llm,
            origin="extracted",
        )

    await asyncio.gather(*(_caption_group(members) for members in groups.values()))
    return ref_mapping


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
        with logfire.span(
            "convert_document",
            filepath=filepath,
            converter=converter.name,
            pipeline=resolved_conversion.value,
        ) as span:
            result = await converter(original_full_path)
            span.set_attribute("markdown_length", len(result.markdown))
            span.set_attribute("image_count", len(result.images))
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

    mode = spec.process_assets
    if mode is AssetProcessingMode.IGNORE:
        markdown = _replace_image_references(
            markdown, {ref: None for ref in result.images}
        )
        has_assets = False
    else:
        ref_mapping = await _process_conversion_assets(
            store,
            workspace_dir,
            assets_dir,
            result.images,
            image_context_windows(markdown),
            mode,
            spec,
            llm,
        )
        markdown = _replace_image_references(markdown, ref_mapping)
        has_assets = bool(result.images)

    chunk_count, chunking_used = await _write_markdown_projection(
        store,
        description_path,
        markdown,
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


async def _delete_single_locked(store: Casebase, safe: str) -> None:
    """Remove a logical entry's files, metadata, and index rows."""
    workspace = store.workspace_dir(settings.data_dir)
    metadata = await db_documents.get_document(store, safe)
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
        await db_documents.delete_subtree(store, assets_rel)

    await _delete_chunked_document(store, safe)


async def _safe_delete_locked(store: Casebase, safe: str) -> None:
    """Best-effort rollback delete.  Swallows the 404 raised when nothing was written."""
    try:
        await _delete_single_locked(store, safe)
    except HTTPException as exc:
        if exc.status_code == 404:
            return
        logger.warning(
            "Rollback delete failed for %s/%s: %s", store.store_key, safe, exc.detail
        )
    except Exception:
        logger.warning(
            "Rollback delete failed for %s/%s", store.store_key, safe, exc_info=True
        )


@asynccontextmanager
async def _rollback_on_failure(
    store: Casebase, touched: Sequence[str]
) -> AsyncIterator[None]:
    """Run a block; on any exception, shield-delete every entry in *touched* and re-raise.

    Caller must hold the casebase lock.  *touched* may be a live list that
    the body appends to — it is read on exit, so accumulating call sites
    work as expected.
    """
    try:
        yield
    except BaseException:

        async def _rollback() -> None:
            for safe in touched:
                await _safe_delete_locked(store, safe)

        await asyncio.shield(_rollback())
        raise


async def _ensure_upload_slot_locked(
    store: Casebase, reference: str, *, overwrite: bool
) -> None:
    """Free up an upload slot for *reference*, raising 409 if occupied."""
    workspace_dir = store.workspace_dir(settings.data_dir)
    if not entry_exists(workspace_dir, reference):
        return
    if not overwrite:
        raise HTTPException(status_code=409, detail="Document already exists")
    await _delete_single_locked(
        store, description_path_for_stem(stem_path_from_reference(reference))
    )


# ---------------------------------------------------------------------------
# Public mutation API
# ---------------------------------------------------------------------------


def _enforce_file_size(content: bytes) -> None:
    """Reject content exceeding the configured maximum upload size."""
    limit = settings.limits.max_file_size_bytes
    if len(content) > limit:
        raise HTTPException(
            status_code=400,
            detail=f"File too large. Maximum size: {limit} bytes",
        )


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

    The casebase lock is held for the entire operation.  On cancellation
    or failure, partial artifacts are rolled back via :func:`_safe_delete_locked`.
    """
    _enforce_file_size(content)
    spec = spec or PipelineSpec()
    llm = llm or LlmConfig()
    async with store_lock(store):
        await _ensure_upload_slot_locked(store, filepath, overwrite=overwrite)
        async with _rollback_on_failure(store, (filepath,)):
            return await _upload_locked(
                store, filepath, content, spec, llm, origin=origin
            )


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
    _enforce_file_size(new_content)
    spec = spec or PipelineSpec()
    llm = llm or LlmConfig()
    async with store_lock(store):
        metadata = await db_documents.get_document(store, safe)
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
        new_original_relpath = f"{stem_path_from_reference(safe)}{new_suffix.lower()}"

        if existing_original_rel != new_original_relpath:
            existing_original_path.unlink(missing_ok=True)

        origin = metadata.origin if metadata else "upload"
        await _clear_assets_subtree(
            store,
            metadata.stem_path if metadata else stem_path_from_reference(safe),
        )
        return await _upload_locked(
            store,
            new_original_relpath,
            new_content,
            spec,
            llm,
            origin=origin,
        )


async def reconvert(
    store: Casebase,
    safe: str,
    *,
    spec: PipelineSpec | None = None,
    llm: LlmConfig | None = None,
) -> UploadCompleteEvent:
    """Re-run conversion and chunking for an entry's existing original.

    The assets-clear runs inside :func:`_rollback_on_failure` so a
    cancel mid-clear (or mid-upload) drops the entry wholesale via
    :func:`_safe_delete_locked`; the user can retry by re-uploading.
    """
    spec = spec or PipelineSpec()
    llm = llm or LlmConfig()
    async with store_lock(store):
        metadata = await db_documents.get_document(store, safe)
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
        async with _rollback_on_failure(store, (safe,)):
            await _clear_assets_subtree(store, metadata.stem_path)
            return await _upload_locked(
                store,
                metadata.original_path,
                original_path.read_bytes(),
                spec,
                llm,
                origin=metadata.origin,
            )


async def rechunk(
    store: Casebase,
    safe: str,
    *,
    spec: PipelineSpec | None = None,
) -> DocumentMetadata:
    """Re-chunk an existing markdown document.

    The chunk + embed + SQL upsert is shielded so a cancel mid-write
    cannot tear the workspace markdown out of sync with SQL.
    """
    spec = spec or PipelineSpec()
    async with store_lock(store):
        workspace_dir = store.workspace_dir(settings.data_dir)
        text = _read_text_file(workspace_dir / safe)
        return await asyncio.shield(
            chunk_and_index_document(store, safe, text, spec.chunking)
        )


def _read_text_file(file_path: Path) -> str:
    if not file_path.is_file():
        raise HTTPException(status_code=404, detail="Document not found")
    return file_path.read_text(encoding="utf-8")


async def _replace_text_locked(
    store: Casebase, safe: str, full_path: Path, content: str
) -> None:
    _enforce_file_size(content.encode("utf-8"))
    full_path.parent.mkdir(parents=True, exist_ok=True)
    full_path.write_text(content, encoding="utf-8")
    await asyncio.shield(chunk_and_index_document(store, safe, content))


async def edit_document_text(
    store: Casebase,
    safe: str,
    old_string: str,
    new_string: str,
    replace_all: bool = False,
) -> str:
    """Edit a workspace text document through the canonical mutation gateway."""
    safe = sanitize_document_path(safe)
    async with store_lock(store):
        workspace_dir = store.workspace_dir(settings.data_dir)
        file_path = workspace_dir / safe
        content = _read_text_file(file_path)
        count = content.count(old_string)
        if count == 0:
            raise HTTPException(
                status_code=400,
                detail=f"old_string not found in '{safe}'",
            )
        if count > 1 and not replace_all:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"old_string appears {count} times in '{safe}'; "
                    "must be unique or call with replace_all=True"
                ),
            )
        new_content = (
            content.replace(old_string, new_string)
            if replace_all
            else content.replace(old_string, new_string, 1)
        )
        await _replace_text_locked(store, safe, file_path, new_content)
    replaced = count if replace_all else 1
    noun = "occurrence" if replaced == 1 else "occurrences"
    return f"Replaced {replaced} {noun} in '{safe}'."


async def write_document_text(
    store: Casebase,
    safe: str,
    content: str,
    mode: str = "replace",
) -> str:
    """Write a workspace text document through the canonical mutation gateway."""
    safe = sanitize_document_path(safe)
    async with store_lock(store):
        workspace_dir = store.workspace_dir(settings.data_dir)
        file_path = workspace_dir / safe
        if mode == "replace":
            new_content = content
            message = f"Wrote {len(content)} characters to '{safe}'."
        elif not file_path.is_file():
            raise HTTPException(
                status_code=404,
                detail=f"'{safe}' does not exist (use mode='replace' to create)",
            )
        elif mode == "append":
            new_content = file_path.read_text(encoding="utf-8") + content
            message = f"Appended {len(content)} characters to '{safe}'."
        elif mode == "prepend":
            new_content = content + file_path.read_text(encoding="utf-8")
            message = f"Prepended {len(content)} characters to '{safe}'."
        else:
            raise HTTPException(
                status_code=400, detail=f"Unsupported write mode: {mode}"
            )
        await _replace_text_locked(store, safe, file_path, new_content)
    return message


async def delete_document(store: Casebase, safe: str) -> None:
    """Delete a logical entry and all of its files."""
    async with store_lock(store):
        await _delete_single_locked(store, safe)


async def move_document(store: Casebase, src: str, dst: str) -> MoveDocumentResponse:
    """Move a logical entry, its original, and its child-assets subtree."""
    async with store_lock(store):
        workspace_dir = store.workspace_dir(settings.data_dir)

        metadata = await db_documents.get_document(store, src)
        if not metadata:
            raise HTTPException(status_code=404, detail="Document not found")

        src_stem = metadata.stem_path or stem_path_from_reference(src)
        dst_stem = stem_path_from_reference(dst)
        if src_stem != dst_stem and entry_exists(workspace_dir, dst):
            raise HTTPException(status_code=409, detail="Destination already exists")

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

        if metadata.original_path:
            src_original_path = workspace_dir / metadata.original_path
            if src_original_path.exists():
                dst_original = f"{dst_stem}{src_original_path.suffix}"
                dst_original_path = workspace_dir / dst_original
                dst_original_path.parent.mkdir(parents=True, exist_ok=True)
                src_original_path.rename(dst_original_path)
                cleanup_empty_parents(src_original_path, workspace_dir)

        src_assets_dir = metadata.assets_dir
        dst_assets_dir: str | None = None
        if src_assets_dir:
            dst_assets_dir = assets_dir_for_stem(dst_stem)
            src_assets_path = workspace_dir / src_assets_dir
            if src_assets_path.exists():
                dst_assets_path = workspace_dir / dst_assets_dir
                dst_assets_path.parent.mkdir(parents=True, exist_ok=True)
                src_assets_path.rename(dst_assets_path)
                cleanup_empty_parents(src_assets_path, workspace_dir)

                src_assets_name = PurePosixPath(src_assets_dir).name
                dst_assets_name = PurePosixPath(dst_assets_dir).name
                if src_assets_name != dst_assets_name:
                    body = dst_description_path.read_text(encoding="utf-8")
                    body = body.replace(f"{src_assets_name}/", f"{dst_assets_name}/")
                    dst_description_path.write_text(body, encoding="utf-8")

        await db_documents.move_subtree(store, src_stem, dst_stem)
        # The described/extracted asset children live under the ``.assets``
        # sibling of ``src_stem``, which the parent subtree filter (matching
        # ``src_stem`` / ``src_stem/…``) never covers — move their rows too so
        # nothing stays searchable at a path that no longer exists.
        if src_assets_dir and dst_assets_dir:
            await db_documents.move_subtree(store, src_assets_dir, dst_assets_dir)

        return MoveDocumentResponse(
            source=src,
            destination=dst_description,
            message="Document moved successfully",
        )


def _resolve_asset_path(assets_path: Path, asset_name: str) -> tuple[str, Path]:
    try:
        safe_name = sanitize_document_path(asset_name)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    if PurePosixPath(safe_name).name != safe_name:
        raise HTTPException(status_code=400, detail="Asset name must be a filename")

    root = assets_path.resolve()
    asset_path = (assets_path / safe_name).resolve()
    if not asset_path.is_relative_to(root):
        raise HTTPException(
            status_code=400,
            detail="Asset path escapes assets directory",
        )
    return safe_name, asset_path


async def _persist_asset_description(
    store: Casebase,
    workspace: Path,
    asset_path: Path,
    safe_name: str,
    content: str,
    size_bytes: int,
) -> AssetEntry:
    """Write a companion ``.md`` file, index it, and return the asset entry.

    Callers pass the asset's *size_bytes* since they already hold either a
    ``stat`` (update) or the raw bytes (generate), avoiding a redundant
    filesystem round-trip here.
    """
    md_path = asset_path.with_suffix(DOCUMENT_EXTENSION)
    md_path.write_text(content, encoding="utf-8")

    description_path = str(md_path.relative_to(workspace).as_posix())
    rel_path = str(asset_path.relative_to(workspace).as_posix())

    await chunk_and_index_document(store, description_path, content)
    return AssetEntry(
        name=safe_name,
        path=rel_path,
        description_path=description_path,
        description=content,
        size_bytes=size_bytes,
        media_type=mimetypes.guess_type(safe_name)[0],
    )


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
    async with store_lock(store):
        workspace = store.workspace_dir(settings.data_dir)
        assets_dir = assets_dir_for_stem(stem_path_from_reference(safe))
        assets_path = workspace / assets_dir
        safe_name, asset_path = _resolve_asset_path(assets_path, asset_name)

        try:
            size_bytes = asset_path.stat().st_size
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Asset file not found") from exc

        return await _persist_asset_description(
            store, workspace, asset_path, safe_name, content, size_bytes
        )


async def generate_asset_description(
    store: Casebase,
    safe: str,
    asset_name: str,
    llm: LlmConfig,
) -> AssetEntry:
    """Describe an asset with the vision model and persist the result.

    Mirrors :func:`update_asset_description` but derives the companion
    ``.md`` content from the asset bytes via the same describe pipeline used
    during ingestion, instead of receiving it from the caller.
    """
    async with store_lock(store):
        workspace = store.workspace_dir(settings.data_dir)
        assets_dir = assets_dir_for_stem(stem_path_from_reference(safe))
        assets_path = workspace / assets_dir
        safe_name, asset_path = _resolve_asset_path(assets_path, asset_name)

        try:
            content_bytes = asset_path.read_bytes()
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Asset file not found") from exc

        media_type = guess_image_media_type(safe_name) or ""
        contexts = [f"File name: {safe_name}"]
        parent_md = workspace / description_path_for_stem(
            stem_path_from_reference(safe)
        )
        if parent_md.exists():
            windows = image_context_windows(parent_md.read_text(encoding="utf-8"))
            contexts.extend(windows.get(asset_ref_for(assets_dir, safe_name), []))
        description = await _build_image_description(
            safe_name, content_bytes, media_type, contexts, llm
        )
        return await _persist_asset_description(
            store, workspace, asset_path, safe_name, description, len(content_bytes)
        )


async def create_directory(store: Casebase, path: str) -> None:
    """Create an empty workspace directory."""
    async with store_lock(store):
        directory_path = store.workspace_dir(settings.data_dir) / path
        if directory_path.exists():
            raise HTTPException(status_code=409, detail="Directory already exists")
        directory_path.mkdir(parents=True, exist_ok=True)


async def move_directory(store: Casebase, src: str, dst: str) -> MoveDirectoryResponse:
    """Move or rename a workspace directory; document rows follow via SQL."""
    async with store_lock(store):
        workspace_dir = store.workspace_dir(settings.data_dir)

        src_dir = workspace_dir / src
        if not src_dir.exists() or not src_dir.is_dir():
            raise HTTPException(status_code=404, detail="Directory not found")
        dst_dir = workspace_dir / dst
        if dst_dir.exists():
            raise HTTPException(status_code=409, detail="Destination already exists")

        files_moved = sum(1 for file_path in src_dir.rglob("*") if file_path.is_file())
        dst_dir.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src_dir), str(dst_dir))
        cleanup_empty_parents(src_dir, workspace_dir)

        await db_documents.move_subtree(store, src, dst)

        return MoveDirectoryResponse(
            source=src,
            destination=dst,
            files_moved=files_moved,
            message="Directory moved successfully",
        )


async def delete_directory(store: Casebase, path: str) -> int:
    """Delete a workspace directory; matching SQL documents cascade out."""
    async with store_lock(store):
        workspace_dir = store.workspace_dir(settings.data_dir)
        directory_path = workspace_dir / path
        if not directory_path.exists() or not directory_path.is_dir():
            raise HTTPException(status_code=404, detail="Directory not found")
        files_deleted = sum(
            1 for file_path in directory_path.rglob("*") if file_path.is_file()
        )
        shutil.rmtree(directory_path)
        cleanup_empty_parents(directory_path, workspace_dir)
        await db_documents.delete_subtree(store, path)
        return files_deleted


async def delete_all(store: Casebase) -> None:
    """Wipe every trace of a casebase: workspace files + SQL rows.

    Chunks cascade-delete with the documents.
    """
    async with store_lock(store):
        await db_documents.delete_all(store)
        workspace_path = store.workspace_path(settings.data_dir)
        if workspace_path.exists():
            shutil.rmtree(workspace_path)


async def delete_workspace_root() -> None:
    """Wipe the entire workspace tree on disk.

    Removes ``<data_dir>/workspace/`` and re-creates the empty root.
    Used by the admin "reset workspace files" action; the caller is
    responsible for clearing the matching SQL documents (cascade then
    drops the vector rows), since this is a filesystem-only operation.
    """
    workspace_root = Casebase.workspace_root(settings.data_dir)

    def _wipe() -> None:
        if workspace_root.exists():
            shutil.rmtree(workspace_root)
        workspace_root.mkdir(parents=True, exist_ok=True)

    await asyncio.to_thread(_wipe)


# ---------------------------------------------------------------------------
# Collection upload
# ---------------------------------------------------------------------------


def _validate_zip_entries(archive: zipfile.ZipFile) -> None:
    """Reject unsafe ZIP entries before extraction.

    Catches symlinks, special files, traversal paths, and zip bombs
    (per-entry and cumulative uncompressed size).
    """
    file_count = 0
    total_uncompressed = 0
    for info in archive.infolist():
        if info.is_dir():
            continue
        file_count += 1
        if file_count > settings.limits.max_collection_files:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Collection has too many files ({file_count}). "
                    f"Maximum: {settings.limits.max_collection_files}"
                ),
            )

        mode = (info.external_attr >> 16) & 0xFFFF
        if mode and not stat.S_ISREG(mode):
            raise HTTPException(
                status_code=400,
                detail=f"ZIP entry {info.filename!r} is not a regular file",
            )

        try:
            sanitize_document_path(info.filename)
        except ValueError as exc:
            raise HTTPException(
                status_code=400,
                detail=f"ZIP contains unsafe path {info.filename!r}: {exc}",
            ) from exc

        if info.file_size > settings.limits.max_file_size_bytes:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"File '{info.filename}' in ZIP is too large "
                    f"({info.file_size} bytes decompressed). "
                    f"Maximum: {settings.limits.max_file_size_bytes} bytes"
                ),
            )
        total_uncompressed += info.file_size
        if total_uncompressed > settings.limits.max_collection_size_bytes:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Collection decompresses to more than "
                    f"{settings.limits.max_collection_size_bytes} bytes"
                ),
            )


async def process_collection(
    store: Casebase,
    raw: bytes,
    spec: PipelineSpec,
    llm: LlmConfig,
) -> AsyncGenerator[CollectionProgressEvent | CollectionCompleteEvent]:
    """Process a ZIP collection and yield progress events for each file.

    The casebase lock is held for the entire collection so a concurrent
    upload elsewhere cannot interleave.  Each per-file upload indexes
    its own chunks via :func:`hivegent.chunks.chunk_and_index_document`,
    and is wrapped in its own :func:`_rollback_on_failure` so only the
    in-flight file is rolled back on cancel — successfully-uploaded
    files survive a client disconnect mid-collection.
    """
    failed: list[str] = []
    markdown_count = 0
    converted_count = 0
    current = 0

    async with store_lock(store):
        with tempfile.TemporaryDirectory() as tmp_dir:
            extract_root = Path(tmp_dir)

            try:
                with zipfile.ZipFile(io.BytesIO(raw)) as archive:
                    _validate_zip_entries(archive)
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
            workspace_dir = store.workspace_dir(settings.data_dir)
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
                        logger.warning("Failed to read %s: %s", relative_path, exc)
                        failed.append(relative_path)
                        continue
                    normalized_md = preprocess_markdown(
                        text, safe, frozenset(collection_files)
                    )
                    preprocessed_markdown[safe] = normalized_md.content.encode("utf-8")

                stem = stem_path_from_reference(safe)
                if entry_exists(workspace_dir, safe):
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
                        async with _rollback_on_failure(store, (safe,)):
                            original_bytes = (extract_root / relative_path).read_bytes()
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
                        content_bytes = (extract_root / relative_path).read_bytes()
                        converted_count += 1
                    async with _rollback_on_failure(store, (safe,)):
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
