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

The filesystem is the source of truth for content; document rows and
chunks are an index derived from it.  Markdown changed or dropped on
disk by hand is folded back into SQL at startup by
:mod:`hivegent.reconcile`, and rows whose description file vanished are
dropped there — workspace files themselves are never deleted outside
the explicit mutation API.
"""

import asyncio
import logging
import mimetypes
import re
import shutil
import stat
import tempfile
import threading
import zipfile
import zlib
from collections.abc import (
    AsyncGenerator,
    AsyncIterator,
    Awaitable,
    Callable,
    Iterable,
    Iterator,
    Sequence,
)
from contextlib import asynccontextmanager, contextmanager
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

import logfire
from fastapi import HTTPException
from pydantic import ValidationError

from .chunkers import ChunkingSpec
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
from .concurrency import shield_to_completion
from .config import content_digest, content_hash, sanitize_document_path, settings
from .converters import (
    ConversionPipeline,
    get_converter,
    resolve_auto_pipeline,
)
from .converters.asset_processing import (
    MD_IMAGE_RE,
    TriageDecision,
    caption_frames,
    caption_image,
    image_context_windows,
    perceptual_key,
    triage_image,
)
from .converters.base import (
    DOCUMENT_EXTENSION,
    ExtractedImage,
    decode_text,
    is_external_ref,
    is_image_suffix,
    is_markdown_suffix,
)
from .converters.images import guess_image_media_type
from .converters.video import (
    animation_frame_count,
    is_video_suffix,
    sample_animated_image,
    sample_video,
)
from .converters.wikilinks import preprocess_markdown
from .db import documents as db_documents
from .entries import (
    ContentStat,
    EntryPaths,
    asset_ref_for,
    assets_dir_for_stem,
    description_path_for_stem,
    entry_exists,
    is_assets_dir,
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
    ProgressReporter,
    UploadCompleteEvent,
    resolve_llm_config,
)

__all__ = [
    "create_directory",
    "delete_all",
    "delete_asset_description",
    "delete_directory",
    "delete_document",
    "delete_workspace_root",
    "edit_document_text",
    "generate_asset_description",
    "inflight_stems",
    "move_directory",
    "move_document",
    "process_collection",
    "prune_empty_dirs",
    "rechunk",
    "reconvert",
    "replace_original",
    "store_lock",
    "sync_entries_from_disk",
    "sync_entry_from_disk",
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


# Stems with an upload currently in flight, per store key.  Inventory reads
# walk the workspace without the casebase lock, so they consult this set to
# hide half-written entries — both during processing and during the rollback
# after a failed or cancelled upload — instead of surfacing them as ghost
# documents.
_inflight_stems: dict[str, set[str]] = {}

# Stores with a bulk import (a collection) in flight, by reference count.  A
# collection commits its files one at a time with the lock released in between,
# so a claim here blocks store-wide destructive ops (delete-all, directory
# delete/move) for its whole duration without blocking its own per-file uploads.
_inflight_store_claims: dict[str, int] = {}


def _add_inflight(store: Casebase, reference: str) -> None:
    """Mark *reference*'s stem as in flight (hidden from lock-free reads)."""
    _inflight_stems.setdefault(store.store_key, set()).add(
        stem_path_from_reference(reference)
    )


def _discard_inflight(store: Casebase, reference: str) -> None:
    """Clear an in-flight mark set by :func:`_add_inflight`."""
    stems = _inflight_stems.get(store.store_key)
    if stems is not None:
        stems.discard(stem_path_from_reference(reference))


@contextmanager
def _store_claim(store: Casebase) -> Iterator[None]:
    """Mark the whole store as having a bulk import in flight for the block.

    Re-entrant (reference counted) so two concurrent collections on one store
    each keep the claim alive until both finish.
    """
    key = store.store_key
    _inflight_store_claims[key] = _inflight_store_claims.get(key, 0) + 1
    try:
        yield
    finally:
        remaining = _inflight_store_claims.get(key, 0) - 1
        if remaining > 0:
            _inflight_store_claims[key] = remaining
        else:
            _inflight_store_claims.pop(key, None)


def inflight_stems(store: Casebase) -> frozenset[str]:
    """Stems with an upload in flight, to be hidden from lock-free reads."""
    return frozenset(_inflight_stems.get(store.store_key, ()))


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


def _entry_metadata_from_disk(
    resolved: EntryPaths, existing: EntryMetadata | None
) -> EntryMetadata:
    """Build current disk metadata, preserving SQL-only provenance when present."""
    return _build_entry_metadata(
        stem_path=resolved.stem_path,
        description_path=resolved.description_path,
        original_path=resolved.original_path,
        assets_dir=resolved.assets_dir,
        entry_kind=existing.entry_kind if existing else "user_markdown",
        origin=existing.origin if existing else "imported",
        generated_by=existing.generated_by if existing else "user",
    )


def _same_persisted_entry_metadata(
    existing: EntryMetadata, current: EntryMetadata
) -> bool:
    """Return whether the SQL-backed entry metadata already matches disk."""
    persisted_fields = set(EntryMetadata.model_fields) - {"files"}
    return existing.model_dump(include=persisted_fields) == current.model_dump(
        include=persisted_fields
    )


async def _refresh_unchanged_entry(
    store: Casebase,
    state: db_documents.EntryState,
    current: EntryMetadata,
    stat: ContentStat | None,
) -> bool:
    """Sync a digest-unchanged entry's metadata + stat key, skipping no-op writes.

    Chunks are never touched here: write only when the companion-file metadata
    or the stat key drifted, so a steady-state boot does no SQL work at all.
    Returns whether SQL changed.
    """
    if (
        _same_persisted_entry_metadata(state.metadata, current)
        and state.content_stat == stat
    ):
        return False
    return await db_documents.update_entry(store, current, stat)


def _write_original_file(workspace_dir: Path, filepath: str, content: bytes) -> Path:
    """Write a binary original file into the workspace."""
    full_path = workspace_dir / filepath
    full_path.parent.mkdir(parents=True, exist_ok=True)
    full_path.write_bytes(content)
    return full_path


def _is_same_file(a: Path, b: Path) -> bool:
    """Whether *a* and *b* are the same inode.

    True for a case-aliased path on a case-insensitive filesystem (macOS,
    Windows), where ``exists()`` alone cannot distinguish "occupied by another
    file" from "the source under its other spelling".  Missing paths are never
    the same file.
    """
    try:
        return a.samefile(b)
    except OSError:
        return False


def _is_blocked_by_other(target: Path, source: Path) -> bool:
    """Whether *target* exists as a node distinct from *source*.

    A target that aliases *source* (a case-only rename on a case-insensitive
    filesystem) is not a blocker, since a plain rename handles it.
    """
    return target.exists() and not _is_same_file(target, source)


def _resolve_move_destination(
    workspace_dir: Path, src_name: str, dst: str, src_path: Path
) -> str:
    """Apply ``mv`` semantics: an existing-directory destination means move into it.

    The source itself is exempt: on a case-insensitive filesystem the
    destination of a case-only rename aliases the source and must stay a plain
    rename instead of nesting the source inside itself.
    """
    dst_path = workspace_dir / dst
    if not dst or (dst_path.is_dir() and not _is_same_file(dst_path, src_path)):
        return str(PurePosixPath(dst) / src_name)
    return dst


def _check_destination_parents(workspace_dir: Path, target: str) -> None:
    """Reject a destination path whose parent chain is blocked by an existing file."""
    blocker = next(
        (
            parent
            for parent in PurePosixPath(target).parents
            if (workspace_dir / parent).is_file()
        ),
        None,
    )
    if blocker is not None:
        raise HTTPException(
            status_code=409, detail=f"Destination parent '{blocker}' is a file"
        )


def _check_not_assets_path(path: str) -> None:
    """Reject paths that reach into the managed ``.assets`` layer.

    ``.assets`` directories are derived storage owned by their document entry
    and hidden from the directory tree, so creating or renaming one through
    the generic directory/move API would silently strand content the UI can
    never show again.
    """
    if any(is_assets_dir(part) for part in PurePosixPath(path).parts):
        raise HTTPException(
            status_code=400,
            detail="'.assets' directories are managed through their owning document",
        )


async def _write_markdown_projection(
    store: Casebase,
    description_path: str,
    content: str,
    spec: PipelineSpec,
    *,
    entry_metadata: EntryMetadata,
) -> tuple[int, str]:
    """Write markdown content and persist chunks (with vectors) in one tx.

    The chunk + embed + SQL upsert step runs to completion even under a cancel
    (:func:`shield_to_completion`), so it finishes while the casebase lock is
    still held and cannot leave the workspace markdown without its SQL rows.  A
    partial markdown file from a hard crash is caught by the startup reconciler.
    """
    workspace_dir = store.workspace_dir(settings.data_dir)
    full_path = workspace_dir / description_path
    full_path.parent.mkdir(parents=True, exist_ok=True)
    full_path.write_text(content, encoding="utf-8")
    chunked = await shield_to_completion(
        chunk_and_index_document(
            store,
            description_path,
            content,
            spec.chunking,
            stat=ContentStat.from_path(full_path),
            entry_metadata=entry_metadata,
        )
    )
    return len(chunked.chunks), chunked.pipeline


async def _describe_with_fallback(
    filepath: str,
    media_kind: str,
    llm: LlmConfig,
    describe: Callable[[LlmConfig], Awaitable[str]],
) -> str:
    """Run *describe* against the aux model, falling back to the file stem.

    Centralizes the description envelope shared by every vision entry:
    resolve the aux config, short-circuit to the stem when no model is
    configured, and turn any failure (or empty output) into the stem so
    the entry still gets a searchable projection.  *media_kind* only
    labels the warning log.
    """
    aux = resolve_llm_config(llm)
    fallback = PurePosixPath(filepath).stem
    if not aux.model:
        return f"{fallback}\n"
    try:
        description = await describe(aux)
    except Exception:
        logger.warning(
            "%s description generation failed for %s",
            media_kind,
            filepath,
            exc_info=True,
        )
        description = fallback
    return f"{description.strip() or fallback}\n"


async def _build_image_description(
    filepath: str,
    content: bytes,
    media_type: str,
    contexts: Sequence[str],
    llm: LlmConfig,
) -> str:
    """Generate markdown describing an image, grounded in *contexts*, with fallback.

    Animated images (multi-frame GIF/WebP) are captioned from frames
    sampled across their timeline rather than from the container bytes —
    vision models would otherwise see only the first frame, and a large
    animation would blow the provider's request size limit.
    """

    async def describe(aux: LlmConfig) -> str:
        if not media_type:
            return ""
        if await asyncio.to_thread(animation_frame_count, content, media_type) > 1:
            sample = await asyncio.to_thread(sample_animated_image, content)
            return await caption_frames(sample, contexts, aux)
        return await caption_image(content, media_type, contexts, aux)

    return await _describe_with_fallback(filepath, "Image", llm, describe)


@dataclass(slots=True, frozen=True)
class _PreparedEntry:
    """A markdown projection to write and index when an upload commits."""

    description_path: str
    markdown: str
    entry_metadata: EntryMetadata


@dataclass(slots=True, frozen=True)
class _PreparedAsset:
    """An extracted asset file to write verbatim when an upload commits."""

    path: str
    data: bytes


@dataclass(slots=True, frozen=True)
class _PreparedUpload:
    """The side-effect-free result of preparing an upload.

    Produced lock-free — the slow work (conversion, vision captioning,
    frame sampling) happens here — then applied to the workspace and SQL
    index atomically under the casebase lock by :func:`_commit_prepared`.
    Holding the lock only for the brief commit, not the whole pipeline, is
    what keeps the rest of the workspace usable while a long upload runs.
    """

    main: _PreparedEntry
    filename: str
    size_bytes: int
    message: str
    converted_filename: str | None = None
    conversion_pipeline_used: str | None = None
    assets: tuple[_PreparedAsset, ...] = ()
    asset_entries: tuple[_PreparedEntry, ...] = ()


@dataclass(slots=True, frozen=True)
class _Reserved:
    """What an upload's locked reserve phase captured for prepare and commit.

    Reserve only validates and reads — it never mutates the workspace — so a
    failure during the lock-free prepare leaves any pre-existing entry intact.
    These fields tell :func:`_commit_prepared` how to apply the new content and
    supersede a prior entry, all atomically under the lock.  ``preserve`` marks a
    reprocess of an existing entry (reconvert/replace/overwrite): its stale
    assets are cleared at commit and it survives a prepare-phase failure, whereas
    a fresh upload (``preserve=False``) is rolled back by deletion.
    """

    reference: str
    content: bytes
    origin: EntryOrigin
    write_original: bool = False
    preserve: bool = False
    supersede_original: str | None = None


@contextmanager
def _source_on_disk(filepath: str, content: bytes) -> Iterator[Path]:
    """Materialise upload bytes at a temp path for converters that read a file.

    Keeps the original basename so format detection by suffix still works, and
    lives outside the workspace so a lock-free conversion never touches the live
    entry — the commit is the only step that writes into the workspace.
    """
    with tempfile.TemporaryDirectory(prefix="hivegent-convert-") as tmp_dir:
        path = Path(tmp_dir) / PurePosixPath(filepath).name
        path.write_bytes(content)
        yield path


def _derived_entry(
    filepath: str,
    markdown: str,
    *,
    entry_kind: EntryKind,
    generated_by: EntryGeneratedBy,
    origin: EntryOrigin,
    assets_dir: str | None = None,
) -> _PreparedEntry:
    """Build a prepared entry whose original is *filepath* and *markdown* its projection.

    Centralises the stem → description derivation shared by every upload kind
    that keeps a separate original (image, video, binary stub, plain text,
    converted document).  User markdown is the exception — its own file is the
    description — so it does not use this.
    """
    stem_path = stem_path_from_reference(filepath)
    description_path = description_path_for_stem(stem_path)
    return _PreparedEntry(
        description_path=description_path,
        markdown=markdown,
        entry_metadata=_build_entry_metadata(
            stem_path=stem_path,
            description_path=description_path,
            original_path=filepath,
            assets_dir=assets_dir,
            entry_kind=entry_kind,
            origin=origin,
            generated_by=generated_by,
        ),
    )


async def _commit_prepared(
    store: Casebase,
    prepared: _PreparedUpload,
    spec: PipelineSpec,
    reserved: _Reserved,
) -> UploadCompleteEvent:
    """Write a prepared upload's files and index its entries. Caller holds the lock.

    This is the only phase that mutates the workspace, so it applies the new
    content and supersedes any prior entry in one locked, cancel-shielded step.
    Old assets are cleared and the main description is *overwritten in place*
    (never deleted first), so even a mid-commit error leaves the entry's
    description as either the old or the new content — never missing.  Asset
    files and their description entries land before the main entry, so the
    markdown that references them is only indexed once its targets exist.
    """
    workspace_dir = store.workspace_dir(settings.data_dir)

    # A reprocess (preserve) supersedes the prior entry: clear its stale assets
    # before writing the new ones. The stem is the reference's stem by construction.
    if reserved.preserve:
        await _clear_assets_subtree(store, stem_path_from_reference(reserved.reference))

    if reserved.write_original:
        _write_original_file(workspace_dir, reserved.reference, reserved.content)

    for asset in prepared.assets:
        _write_original_file(workspace_dir, asset.path, asset.data)

    for entry in prepared.asset_entries:
        await _write_markdown_projection(
            store,
            entry.description_path,
            entry.markdown,
            spec,
            entry_metadata=entry.entry_metadata,
        )

    chunk_count, chunking_used = await _write_markdown_projection(
        store,
        prepared.main.description_path,
        prepared.main.markdown,
        spec,
        entry_metadata=prepared.main.entry_metadata,
    )

    # A superseded original on a different path than the new one (a replace that
    # changed the suffix) is unlinked only after the new entry is fully written.
    if (
        reserved.supersede_original is not None
        and reserved.supersede_original != reserved.reference
    ):
        (workspace_dir / reserved.supersede_original).unlink(missing_ok=True)

    return UploadCompleteEvent(
        filename=prepared.filename,
        converted_filename=prepared.converted_filename,
        size_bytes=prepared.size_bytes,
        conversion_pipeline_used=prepared.conversion_pipeline_used,
        chunk_count=chunk_count,
        chunking_pipeline_used=chunking_used,
        message=prepared.message,
    )


async def _prepare_image_entry(
    filepath: str,
    content: bytes,
    media_type: str,
    contexts: Sequence[str],
    llm: LlmConfig,
    *,
    origin: EntryOrigin,
) -> _PreparedEntry:
    """Build the caption entry for an image without touching disk or SQL.

    Shared by standalone image uploads and the described assets extracted from
    a converted document; *contexts* carries every occurrence's surrounding
    text so the caption is the single source of truth for that image.
    """
    markdown = await _build_image_description(
        filepath, content, media_type, contexts, llm
    )
    return _derived_entry(
        filepath, markdown, entry_kind="image", generated_by="vision", origin=origin
    )


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
    await db_documents.delete_subtree(store, assets_dir)


# ---------------------------------------------------------------------------
# Per-kind preparation (lock-free)
# ---------------------------------------------------------------------------


def _prepare_markdown(
    store: Casebase,
    filepath: str,
    content: bytes,
    *,
    origin: EntryOrigin,
    clearing_assets: bool,
) -> _PreparedUpload:
    """Prepare a user-authored markdown document for commit.

    A markdown entry surfaces a companion ``.assets`` directory if one exists,
    except when the commit will clear it (an overwrite), so the metadata never
    claims a directory the same commit is about to remove.
    """
    workspace_dir = store.workspace_dir(settings.data_dir)
    text = content.decode("utf-8")
    stem_path = stem_path_from_reference(filepath)
    assets_dir = assets_dir_for_stem(stem_path)
    has_assets = not clearing_assets and (workspace_dir / assets_dir).exists()
    main = _PreparedEntry(
        description_path=filepath,
        markdown=text,
        entry_metadata=_build_entry_metadata(
            stem_path=stem_path,
            description_path=filepath,
            original_path=None,
            assets_dir=assets_dir if has_assets else None,
            entry_kind="user_markdown",
            origin=origin,
            generated_by="user",
        ),
    )
    return _PreparedUpload(
        main=main,
        filename=filepath,
        size_bytes=len(content),
        message="Document uploaded successfully",
    )


async def _prepare_image(
    filepath: str,
    content: bytes,
    llm: LlmConfig,
    *,
    origin: EntryOrigin,
    ctx: ProgressReporter | None,
) -> _PreparedUpload:
    """Prepare a standalone image and its generated description for commit."""
    if ctx is not None:
        ctx.set_stage("Generating image description")

    media_type = guess_image_media_type(filepath) or ""
    entry = await _prepare_image_entry(
        filepath,
        content,
        media_type,
        [f"File name: {PurePosixPath(filepath).name}"],
        llm,
        origin=origin,
    )
    return _PreparedUpload(
        main=entry,
        filename=filepath,
        size_bytes=len(content),
        converted_filename=entry.description_path,
        message="Image uploaded and described successfully",
    )


async def _build_video_description(
    filepath: str,
    full_path: Path,
    contexts: Sequence[str],
    llm: LlmConfig,
) -> str:
    """Generate markdown describing a video from sampled frames, with fallback."""

    async def describe(aux: LlmConfig) -> str:
        sample = await sample_video(full_path)
        return await caption_frames(sample, contexts, aux)

    return await _describe_with_fallback(filepath, "Video", llm, describe)


async def _prepare_video(
    filepath: str,
    content: bytes,
    llm: LlmConfig,
    *,
    origin: EntryOrigin,
    ctx: ProgressReporter | None,
) -> _PreparedUpload:
    """Prepare a video and its frame-based description for commit.

    The original is the entry's payload and the vision-generated markdown is
    its searchable projection.  Frames are sampled via ffmpeg from a temp copy
    of the source (see :func:`~hivegent.converters.video.sample_video`), so the
    lock-free prepare never touches the live workspace entry.
    """
    if ctx is not None:
        ctx.set_stage("Generating video description")

    with _source_on_disk(filepath, content) as full_path:
        markdown = await _build_video_description(
            filepath,
            full_path,
            [f"File name: {PurePosixPath(filepath).name}"],
            llm,
        )
    main = _derived_entry(
        filepath, markdown, entry_kind="video", generated_by="vision", origin=origin
    )
    return _PreparedUpload(
        main=main,
        filename=filepath,
        size_bytes=len(content),
        converted_filename=main.description_path,
        message="Video uploaded and described successfully",
    )


def _prepare_binary_stub(
    filepath: str, content: bytes, *, origin: EntryOrigin
) -> _PreparedUpload:
    """Prepare a searchable stub for a non-convertible binary."""
    main = _derived_entry(
        filepath,
        _build_binary_stub(filepath, len(content)),
        entry_kind="binary_stub",
        generated_by="stub",
        origin=origin,
    )
    return _PreparedUpload(
        main=main,
        filename=filepath,
        size_bytes=len(content),
        converted_filename=main.description_path,
        message="Binary file uploaded with searchable stub",
    )


def _prepare_unconvertible(
    filepath: str, content: bytes, *, origin: EntryOrigin
) -> _PreparedUpload:
    """AUTO fallback when no converter fits the file.

    Bytes that decode as UTF-8 are prepared as a plain-text document so their
    content stays searchable; genuinely binary bytes get a metadata-only stub.
    The reserve step has already written the original to the workspace.
    """
    text = decode_text(content)
    if text is None:
        return _prepare_binary_stub(filepath, content, origin=origin)

    main = _derived_entry(
        filepath,
        text,
        entry_kind="convertible",
        generated_by="converter",
        origin=origin,
    )
    return _PreparedUpload(
        main=main,
        filename=filepath,
        size_bytes=len(content),
        converted_filename=main.description_path,
        conversion_pipeline_used=ConversionPipeline.TEXT_CHEF.value,
        message="Document uploaded as plain text",
    )


async def _prepare_conversion_assets(
    assets_dir: str,
    images: dict[str, ExtractedImage],
    contexts_by_ref: dict[str, list[str]],
    mode: AssetProcessingMode,
    llm: LlmConfig,
) -> tuple[dict[str, str | None], list[_PreparedAsset], list[_PreparedEntry]]:
    """Triage, deduplicate, and caption a conversion's extracted images.

    Lock-free: returns the markdown reference remapping plus the asset files
    and caption entries to apply at commit time, without touching disk or SQL.
    Store-only assets (decorative, or ``STORE`` mode) keep their own reference;
    described assets are grouped by :func:`perceptual_key` so an image is
    captioned once and every occurrence's reference is rewritten to the single
    stored representative — never once per occurrence.
    """

    def child_path(relpath: str) -> str:
        return str((PurePosixPath(assets_dir) / relpath).as_posix())

    ref_mapping: dict[str, str | None] = {}
    assets: list[_PreparedAsset] = []
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
            assets.append(_PreparedAsset(child_path(relpath), extracted.data))
            continue

        key = perceptual_key(extracted.data)
        groups.setdefault(key if key is not None else object(), []).append(relpath)

    for members in groups.values():
        rep_ref = asset_ref_for(assets_dir, members[0])
        for member in members:
            ref_mapping[member] = rep_ref

    asset_entries: list[_PreparedEntry] = []

    async def _caption_group(members: list[str]) -> None:
        representative = members[0]
        rep_path = child_path(representative)
        media_type = guess_image_media_type(representative) or ""
        contexts: list[str] = []
        for member in members:
            contexts.extend(contexts_by_ref.get(member, []))
            if caption := images[member].caption:
                contexts.append(f"Figure caption: {caption}")
        assets.append(_PreparedAsset(rep_path, images[representative].data))
        asset_entries.append(
            await _prepare_image_entry(
                rep_path,
                images[representative].data,
                media_type,
                contexts,
                llm,
                origin="extracted",
            )
        )

    await asyncio.gather(*(_caption_group(members) for members in groups.values()))
    return ref_mapping, assets, asset_entries


async def _prepare_convertible(
    filepath: str,
    content: bytes,
    spec: PipelineSpec,
    llm: LlmConfig,
    *,
    origin: EntryOrigin,
    ctx: ProgressReporter | None,
) -> _PreparedUpload:
    """Convert a binary and prepare its markdown plus extracted assets.

    Runs the converter against a temp copy of the source, then prepares the
    asset files and caption entries — all without the casebase lock and without
    touching the live workspace entry, so a long conversion never blocks the
    rest of the workspace and a failure mid-conversion leaves nothing behind.
    """
    if ctx is not None:
        ctx.set_stage("Processing document")

    basename = PurePosixPath(filepath).name
    conversion_pipeline = spec.conversion.pipeline

    try:
        converter = get_converter(
            conversion_pipeline,
            filename=basename,
            config=spec.conversion.config,
            llm_options=llm,
            detect_asset_roles=spec.process_assets is AssetProcessingMode.DESCRIBE,
        )
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=exc.errors()) from exc
    except (ImportError, ValueError) as exc:
        if conversion_pipeline == ConversionPipeline.AUTO:
            return _prepare_unconvertible(filepath, content, origin=origin)
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    resolved_conversion = conversion_pipeline
    if conversion_pipeline == ConversionPipeline.AUTO:
        resolved_conversion = resolve_auto_pipeline(basename)

    try:
        with (
            logfire.span(
                "convert_document",
                filepath=filepath,
                converter=converter.name,
                pipeline=resolved_conversion.value,
            ) as span,
            _source_on_disk(filepath, content) as source_path,
        ):
            result = await converter(source_path)
            span.set_attribute("markdown_length", len(result.markdown))
            span.set_attribute("image_count", len(result.images))
    except Exception as exc:
        if conversion_pipeline == ConversionPipeline.AUTO:
            # exc_info captures the chained cause: docling re-raises pipeline
            # errors as a bare "Pipeline ... failed" with the root cause only
            # attached via `from`.
            logger.warning(
                "AUTO conversion failed for %s, indexing as plain text or stub: %s",
                filepath,
                exc,
                exc_info=exc,
            )
            return _prepare_unconvertible(filepath, content, origin=origin)
        raise HTTPException(
            status_code=500,
            detail=f"Conversion failed: {exc!s}",
        ) from exc

    assets_dir = assets_dir_for_stem(stem_path_from_reference(filepath))
    markdown = result.markdown

    mode = spec.process_assets
    assets: tuple[_PreparedAsset, ...] = ()
    asset_entries: tuple[_PreparedEntry, ...] = ()
    if mode is AssetProcessingMode.IGNORE:
        markdown = _replace_image_references(
            markdown, {ref: None for ref in result.images}
        )
        has_assets = False
    else:
        if ctx is not None and mode is AssetProcessingMode.DESCRIBE and result.images:
            ctx.set_stage("Describing images")
        ref_mapping, asset_list, entry_list = await _prepare_conversion_assets(
            assets_dir,
            result.images,
            image_context_windows(markdown),
            mode,
            llm,
        )
        markdown = _replace_image_references(markdown, ref_mapping)
        has_assets = bool(result.images)
        assets = tuple(asset_list)
        asset_entries = tuple(entry_list)

    main = _derived_entry(
        filepath,
        markdown,
        entry_kind="convertible",
        generated_by="converter",
        origin=origin,
        assets_dir=assets_dir if has_assets else None,
    )
    return _PreparedUpload(
        main=main,
        filename=filepath,
        size_bytes=len(content),
        converted_filename=main.description_path,
        conversion_pipeline_used=resolved_conversion.value,
        assets=assets,
        asset_entries=asset_entries,
        message="Document uploaded and converted successfully",
    )


async def _prepare_upload(
    store: Casebase,
    filepath: str,
    content: bytes,
    spec: PipelineSpec,
    llm: LlmConfig,
    *,
    origin: EntryOrigin,
    ctx: ProgressReporter | None,
    clearing_assets: bool,
) -> _PreparedUpload:
    """Dispatch to the per-kind preparation. No lock held."""
    suffix = PurePosixPath(filepath).suffix.lower()
    if is_markdown_suffix(suffix):
        return _prepare_markdown(
            store, filepath, content, origin=origin, clearing_assets=clearing_assets
        )
    if is_image_suffix(suffix):
        return await _prepare_image(filepath, content, llm, origin=origin, ctx=ctx)
    if is_video_suffix(suffix):
        return await _prepare_video(filepath, content, llm, origin=origin, ctx=ctx)
    return await _prepare_convertible(
        filepath, content, spec, llm, origin=origin, ctx=ctx
    )


async def _delete_single_locked(store: Casebase, safe: str) -> None:
    """Remove a logical entry's files, metadata, and index rows.

    Works for entries without a SQL row or description file too (e.g. a
    stray original that was never ingested), so any on-disk entry can
    always be removed through the API.
    """
    workspace = store.workspace_dir(settings.data_dir)
    metadata = await db_documents.get_document(store, safe)
    if not metadata and not entry_exists(workspace, safe):
        raise HTTPException(status_code=404, detail="Document not found")

    resolved = resolve_entry_paths(workspace, safe)
    description_rel = (
        metadata.description_path
        if metadata and metadata.description_path
        else resolved.description_path
    )
    description_path = workspace / description_rel
    if description_path.exists():
        description_path.unlink()

    original_rel = metadata.original_path if metadata else resolved.original_path
    if original_rel:
        original_path = workspace / original_rel
        if original_path.exists():
            original_path.unlink()

    assets_rel = metadata.assets_dir if metadata else resolved.assets_dir
    if assets_rel:
        assets_path = workspace / assets_rel
        if assets_path.exists():
            shutil.rmtree(assets_path)
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
    """Run a block; on any exception, delete every entry in *touched* and re-raise.

    Caller must hold the casebase lock.  *touched* may be a live list that
    the body appends to — it is read on exit, so accumulating call sites
    work as expected.  The rollback runs to completion even when the failure is
    a cancellation (:func:`shield_to_completion`), so the partial artifacts are
    gone before the caller's lock is released — a bare ``asyncio.shield`` would
    detach the rollback and let a subsequent operation acquire the lock and race
    the still-running deletes.
    """
    try:
        yield
    except BaseException:

        async def _rollback() -> None:
            for safe in touched:
                await _safe_delete_locked(store, safe)

        await shield_to_completion(_rollback())
        raise


def _ensure_upload_slot_locked(
    store: Casebase, reference: str, *, overwrite: bool
) -> None:
    """Validate that *reference*'s slot can be written, raising 409 if blocked.

    Rejects a destination whose parent chain is a file, a target occupied by a
    directory, and a non-overwrite write onto an existing entry.  It performs no
    deletion: an overwrite's stale parts are superseded atomically at commit
    (see :func:`_commit_prepared`), so a failed or cancelled conversion can
    never destroy the prior entry.
    """
    workspace_dir = store.workspace_dir(settings.data_dir)
    _check_destination_parents(workspace_dir, reference)
    stem_path = stem_path_from_reference(reference)
    for rel in {reference, description_path_for_stem(stem_path)}:
        if (workspace_dir / rel).is_dir():
            raise HTTPException(
                status_code=409, detail=f"'{rel}' is an existing directory"
            )
    if entry_exists(workspace_dir, reference) and not overwrite:
        raise HTTPException(status_code=409, detail="Document already exists")


# ---------------------------------------------------------------------------
# Disk → SQL entry sync (reconciler today, shell-tool fold-back later)
# ---------------------------------------------------------------------------


async def _sync_entry_from_disk_locked(store: Casebase, reference: str) -> bool:
    """Re-derive one logical entry's SQL + chunk rows from its on-disk markdown.

    The single idempotent ingest path: drop the row if the description is gone;
    skip an untouched description via a cheap ``(mtime, size)`` stat fast-path;
    re-stamp metadata/stat when only companion files or the stat moved; chunk,
    embed, and upsert only when the content digest actually changed.  An entry
    with no prior SQL row is stamped ``origin="imported"`` since its provenance
    cannot be recovered from disk, an existing entry keeps its stored
    provenance.  Returns whether SQL changed.  Caller must hold the casebase
    lock.
    """
    workspace_dir = store.workspace_dir(settings.data_dir)
    resolved = resolve_entry_paths(workspace_dir, reference)
    description_full = workspace_dir / resolved.description_path

    if not description_full.is_file():
        # Entry gone on disk: drop the row if one exists (chunks cascade).
        return await _delete_chunked_document(store, resolved.description_path)

    state = await db_documents.get_entry_state(store, resolved.description_path)
    existing = state.metadata if state else None
    entry_metadata = _entry_metadata_from_disk(resolved, existing)
    stat = ContentStat.from_path(description_full)

    # Fast path: a stored stat equal to the file's stat means the digest cannot
    # have changed, so skip the read + hash and only reconcile companion-file
    # metadata.  The stat is only ever stamped together with a digest, so its
    # presence implies an indexed row.
    if (
        state is not None
        and state.content_digest is not None
        and stat is not None
        and state.content_stat == stat
    ):
        return await _refresh_unchanged_entry(store, state, entry_metadata, stat)

    content = description_full.read_text(encoding="utf-8")
    digest = content_digest(content)
    if state is not None and state.content_digest == digest:
        # Content identical despite a moved stat (touch, checkout, restore):
        # persist the fresh stat so the next boot hits the fast path, plus any
        # companion-metadata drift.  No re-embed.
        return await _refresh_unchanged_entry(store, state, entry_metadata, stat)

    await shield_to_completion(
        chunk_and_index_document(
            store,
            resolved.description_path,
            content,
            stat=stat,
            entry_metadata=entry_metadata,
        )
    )
    return True


async def sync_entry_from_disk(store: Casebase, reference: str) -> bool:
    """Bring one logical entry's SQL state into agreement with its disk bytes.

    Lock-acquiring form of :func:`_sync_entry_from_disk_locked`.  Returns
    whether SQL changed.
    """
    async with store_lock(store):
        return await _sync_entry_from_disk_locked(store, reference)


async def sync_entries_from_disk(store: Casebase, references: Iterable[str]) -> int:
    """Fold a batch of on-disk entry changes into SQL under one lock.

    The fold-back primitive a future read-write shell tool calls once a
    session ends: pass every touched description path and SQL is re-derived
    from the current bytes in a single locked, idempotent pass.  Reused by the
    startup reconciler.  Returns the number of entries whose index changed.
    """
    async with store_lock(store):
        changed = 0
        for reference in references:
            if await _sync_entry_from_disk_locked(store, reference):
                changed += 1
        return changed


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


def _reject_if_inflight(store: Casebase, reference: str) -> None:
    """Reject a mutation whose stem another phased upload already has in flight.

    A phased upload marks its stem the moment it claims the entry, so a second
    op on that stem 409s instead of racing the pending commit.  A markdown
    upload writes nothing to disk during reserve, so the in-flight set is the
    only thing that closes the window for it.
    """
    if stem_path_from_reference(reference) in inflight_stems(store):
        raise HTTPException(
            status_code=409, detail="Document is already being processed"
        )


def _reject_if_scope_inflight(store: Casebase, prefix: str | None) -> None:
    """Reject a directory- or store-wide mutation while work inside it runs.

    *prefix* is the directory whose contents the op removes or moves, or
    ``None`` for the whole store (delete-all).  A phased upload commits lock-free
    between its reserve and commit, and a bulk import commits its files one at a
    time; tearing down an enclosing directory in either window would strip files
    out from under a pending commit (orphaning an entry, or resurrecting one
    after a wipe).  The 409 defers the op until the in-flight work settles.
    """
    if _inflight_store_claims.get(store.store_key, 0) > 0:
        raise HTTPException(
            status_code=409, detail="A document in this scope is still being processed"
        )

    stems = inflight_stems(store)
    blocked = (
        stems if prefix is None else {s for s in stems if s.startswith(f"{prefix}/")}
    )
    if blocked:
        raise HTTPException(
            status_code=409, detail="A document in this scope is still being processed"
        )


@asynccontextmanager
async def _locked_for(
    store: Casebase,
    *entries: str,
    scope: str | None = None,
    whole_store: bool = False,
) -> AsyncIterator[None]:
    """Acquire the casebase lock for a mutation, rejecting in-flight conflicts.

    Routing every mutation's lock acquisition through here makes the in-flight
    check impossible to forget: pass the entry references a single-document op
    touches, ``scope`` for a directory subtree it removes or moves, or
    ``whole_store`` for a store-wide wipe.  A conflicting phased upload (or a
    bulk import claiming the store) is rejected with 409 so the op can never
    strip files out from under a pending commit.
    """
    async with store_lock(store):
        for entry in entries:
            _reject_if_inflight(store, entry)
        if whole_store:
            _reject_if_scope_inflight(store, None)
        elif scope is not None:
            _reject_if_scope_inflight(store, scope)

        yield


type _Reserve = Callable[[], Awaitable[_Reserved]]


async def _phased_upload(
    store: Casebase,
    spec: PipelineSpec,
    llm: LlmConfig,
    *,
    stem_reference: str,
    reserve: _Reserve,
    ctx: ProgressReporter | None,
) -> UploadCompleteEvent:
    """Run an upload's reserve → prepare → commit phases.

    The lock is held only for the brief *reserve* (validate + capture) and the
    final *commit* (apply); the slow *prepare* (conversion, captioning) runs
    lock-free in between against a temp copy of the source, so it never touches
    the live workspace.  Because nothing is written until commit, a failure or
    cancellation during prepare leaves a pre-existing entry (``preserve``)
    completely intact; only a genuinely new entry is rolled back by deleting it.
    *stem_reference* is the stem this upload owns for its whole lifecycle.
    """
    claimed = False
    reserved: _Reserved | None = None
    try:
        async with _locked_for(store, stem_reference):
            reserved = await reserve()
            _add_inflight(store, stem_reference)
            claimed = True

        prepared = await _prepare_upload(
            store,
            reserved.reference,
            reserved.content,
            spec,
            llm,
            origin=reserved.origin,
            ctx=ctx,
            clearing_assets=reserved.preserve,
        )
        async with store_lock(store):
            return await shield_to_completion(
                _commit_prepared(store, prepared, spec, reserved)
            )
    except BaseException:
        # A new entry's partial artifacts are rolled back; a pre-existing entry
        # is left untouched (prepare never wrote into the workspace).
        if reserved is not None and not reserved.preserve:
            async with store_lock(store):
                await shield_to_completion(
                    _safe_delete_locked(store, reserved.reference)
                )
        raise
    finally:
        if claimed:
            _discard_inflight(store, stem_reference)


async def upload(
    store: Casebase,
    filepath: str,
    content: bytes,
    *,
    spec: PipelineSpec | None = None,
    llm: LlmConfig | None = None,
    origin: EntryOrigin = "upload",
    overwrite: bool = False,
    ctx: ProgressReporter | None = None,
) -> UploadCompleteEvent:
    """Upload a document to *store*, converting and chunking as needed.

    See :func:`_phased_upload` for the reserve/prepare/commit lifecycle; here
    reserve only validates the slot and captures the source, and an overwrite
    supersedes the prior entry atomically at commit, so a failed conversion
    never destroys it.
    """
    if not filepath:
        raise HTTPException(status_code=400, detail="Document path required")
    _enforce_file_size(content)
    spec = spec or PipelineSpec()
    llm = llm or LlmConfig()
    is_markdown = is_markdown_suffix(PurePosixPath(filepath).suffix.lower())

    async def reserve() -> _Reserved:
        _ensure_upload_slot_locked(store, filepath, overwrite=overwrite)
        workspace_dir = store.workspace_dir(settings.data_dir)
        replacing = overwrite and entry_exists(workspace_dir, filepath)
        supersede = None
        if replacing:
            metadata = await db_documents.get_document(store, filepath)
            supersede = (
                metadata.original_path
                if metadata
                else resolve_entry_paths(workspace_dir, filepath).original_path
            )

        return _Reserved(
            reference=filepath,
            content=content,
            origin=origin,
            write_original=not is_markdown,
            preserve=replacing,
            supersede_original=supersede,
        )

    return await _phased_upload(
        store, spec, llm, stem_reference=filepath, reserve=reserve, ctx=ctx
    )


async def replace_original(
    store: Casebase,
    safe: str,
    new_content: bytes,
    *,
    new_filename: str | None,
    spec: PipelineSpec | None = None,
    llm: LlmConfig | None = None,
    ctx: ProgressReporter | None = None,
) -> UploadCompleteEvent:
    """Replace the original file backing a logical entry and reconvert.

    The new original keeps the entry's stem; only the suffix may change.  See
    :func:`_phased_upload` for the lifecycle; the swap (new original written,
    stale assets cleared, old original unlinked) happens atomically at commit,
    so a failed conversion or a cancel leaves the prior entry intact.
    """
    _enforce_file_size(new_content)
    spec = spec or PipelineSpec()
    llm = llm or LlmConfig()

    async def reserve() -> _Reserved:
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

        existing_suffix = PurePosixPath(existing_original_rel).suffix
        new_suffix = (
            PurePosixPath(new_filename).suffix if new_filename else existing_suffix
        ) or existing_suffix
        new_original_relpath = f"{stem_path_from_reference(safe)}{new_suffix.lower()}"

        return _Reserved(
            reference=new_original_relpath,
            content=new_content,
            origin=metadata.origin if metadata else "upload",
            write_original=True,
            preserve=True,
            supersede_original=existing_original_rel,
        )

    return await _phased_upload(
        store, spec, llm, stem_reference=safe, reserve=reserve, ctx=ctx
    )


async def reconvert(
    store: Casebase,
    safe: str,
    *,
    spec: PipelineSpec | None = None,
    llm: LlmConfig | None = None,
    ctx: ProgressReporter | None = None,
) -> UploadCompleteEvent:
    """Re-run conversion and chunking for an entry's existing original.

    See :func:`_phased_upload` for the lifecycle; reserve only reads the
    existing original, and the stale assets are cleared atomically at commit.
    A cancel or a failed conversion therefore leaves the entry exactly as it
    was, so the user can simply retry.
    """
    spec = spec or PipelineSpec()
    llm = llm or LlmConfig()

    async def reserve() -> _Reserved:
        metadata = await db_documents.get_document(store, safe)
        if not metadata or not metadata.original_path:
            raise HTTPException(
                status_code=404,
                detail=f"No original file found for '{safe}'",
            )
        original_full = store.workspace_dir(settings.data_dir) / metadata.original_path
        if not original_full.exists():
            raise HTTPException(
                status_code=404,
                detail=f"No original file found for '{safe}'",
            )

        return _Reserved(
            reference=metadata.original_path,
            content=original_full.read_bytes(),
            origin=metadata.origin,
            write_original=False,
            preserve=True,
        )

    return await _phased_upload(
        store, spec, llm, stem_reference=safe, reserve=reserve, ctx=ctx
    )


async def rechunk(
    store: Casebase,
    safe: str,
    *,
    spec: PipelineSpec | None = None,
) -> DocumentMetadata:
    """Re-chunk an existing markdown document.

    The chunk + embed + SQL upsert runs to completion even on a cancel
    (:func:`shield_to_completion`), so it finishes under the lock and cannot
    tear the workspace markdown out of sync with SQL.
    """
    spec = spec or PipelineSpec()
    async with _locked_for(store, safe):
        file_path = store.workspace_dir(settings.data_dir) / safe
        text = _read_text_file(file_path)
        return await shield_to_completion(
            chunk_and_index_document(
                store, safe, text, spec.chunking, stat=ContentStat.from_path(file_path)
            )
        )


def _read_text_file(file_path: Path) -> str:
    if not file_path.is_file():
        raise HTTPException(status_code=404, detail="Document not found")
    return file_path.read_text(encoding="utf-8")


def _check_expected_hash(
    safe: str, current: str | None, expected_hash: str | None
) -> None:
    """Reject the mutation unless *current* still matches *expected_hash*.

    The optimistic-concurrency guard: a non-``None`` *expected_hash* comes
    from an earlier read. A mismatch means the document moved on since, and a
    missing file (*current* is ``None``) means the caller never read it at all,
    so the hash is hallucinated. Both are rejected with a 409.
    """
    if expected_hash is None:
        return
    if current is None:
        raise HTTPException(
            status_code=409,
            detail=(
                f"'{safe}' does not exist, so it could not have been read "
                f"(expected hash {expected_hash}); omit expected_hash to create it"
            ),
        )
    if (actual := content_hash(current)) != expected_hash:
        raise HTTPException(
            status_code=409,
            detail=(
                f"'{safe}' changed since it was read "
                f"(expected hash {expected_hash}, found {actual}); "
                "re-read it and retry with the new hash"
            ),
        )


async def _replace_text_locked(
    store: Casebase,
    safe: str,
    full_path: Path,
    content: str,
    chunking: ChunkingSpec | None = None,
) -> None:
    _enforce_file_size(content.encode("utf-8"))
    if full_path.is_dir():
        raise HTTPException(status_code=400, detail=f"'{safe}' is a directory")
    _check_destination_parents(store.workspace_dir(settings.data_dir), safe)
    full_path.parent.mkdir(parents=True, exist_ok=True)
    full_path.write_text(content, encoding="utf-8")
    await shield_to_completion(
        chunk_and_index_document(
            store, safe, content, chunking, stat=ContentStat.from_path(full_path)
        )
    )


async def edit_document_text(
    store: Casebase,
    safe: str,
    old_string: str,
    new_string: str,
    replace_all: bool = False,
    expected_hash: str | None = None,
) -> str:
    """Edit a workspace text document through the canonical mutation gateway."""
    safe = sanitize_document_path(safe)
    async with _locked_for(store, safe):
        workspace_dir = store.workspace_dir(settings.data_dir)
        file_path = workspace_dir / safe
        content = _read_text_file(file_path)
        _check_expected_hash(safe, content, expected_hash)
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
    expected_hash: str | None = None,
    chunking: ChunkingSpec | None = None,
) -> str:
    """Write a workspace text document through the canonical mutation gateway."""
    safe = sanitize_document_path(safe)
    async with _locked_for(store, safe):
        workspace_dir = store.workspace_dir(settings.data_dir)
        file_path = workspace_dir / safe
        current = file_path.read_text(encoding="utf-8") if file_path.is_file() else None
        _check_expected_hash(safe, current, expected_hash)
        if mode == "replace":
            new_content = content
            message = f"Wrote {len(content)} characters to '{safe}'."
        elif mode == "create":
            if current is not None:
                raise HTTPException(
                    status_code=409, detail=f"'{safe}' already exists"
                )
            new_content = content
            message = f"Created '{safe}' with {len(content)} characters."
        elif current is None:
            raise HTTPException(
                status_code=404,
                detail=f"'{safe}' does not exist (use mode='replace' to create)",
            )
        elif mode == "append":
            new_content = current + content
            message = f"Appended {len(content)} characters to '{safe}'."
        elif mode == "prepend":
            new_content = content + current
            message = f"Prepended {len(content)} characters to '{safe}'."
        else:
            raise HTTPException(
                status_code=400, detail=f"Unsupported write mode: {mode}"
            )
        await _replace_text_locked(store, safe, file_path, new_content, chunking)
    return message


async def delete_document(store: Casebase, safe: str) -> None:
    """Delete a logical entry and all of its files.

    The file + SQL removal runs to completion under the lock even on a cancel
    (:func:`shield_to_completion`) so it cannot leave files without their rows
    or rows without their files.
    """
    async with _locked_for(store, safe):
        await shield_to_completion(_delete_single_locked(store, safe))


async def _move_document_locked(
    store: Casebase, src: str, dst: str
) -> MoveDocumentResponse:
    """Move a logical entry's files and SQL rows. Caller holds the lock."""
    workspace_dir = store.workspace_dir(settings.data_dir)

    metadata = await db_documents.get_document(store, src)
    if not metadata or not (workspace_dir / metadata.description_path).exists():
        raise HTTPException(status_code=404, detail="Document not found")
    src_stem = metadata.stem_path
    src_description_full = workspace_dir / metadata.description_path

    # Move-into resolution appends the description *filename* (a reference),
    # never the bare stem name: ``stem_path_from_reference`` strips the last
    # dotted segment, so a stem like ``report.v1`` passed back through it
    # would collapse to ``report``.
    dst_stem = stem_path_from_reference(
        _resolve_move_destination(
            workspace_dir,
            PurePosixPath(metadata.description_path).name,
            dst,
            src_description_full,
        )
    )
    _check_not_assets_path(dst_stem)
    if dst_stem == src_stem:
        raise HTTPException(
            status_code=400,
            detail=(
                "Source and destination refer to the same document; "
                "extensions are derived from the entry and cannot change by moving"
            ),
        )

    dst_description = description_path_for_stem(dst_stem)

    # Plan every rename up front and validate all destinations before touching
    # the filesystem: a conflict discovered halfway through the renames would
    # tear the entry into two half-moved halves.
    renames: list[tuple[str, str]] = [(metadata.description_path, dst_description)]
    if metadata.original_path and (workspace_dir / metadata.original_path).exists():
        suffix = PurePosixPath(metadata.original_path).suffix
        renames.append((metadata.original_path, f"{dst_stem}{suffix}"))
    has_assets = (
        metadata.assets_dir is not None
        and (workspace_dir / metadata.assets_dir).exists()
    )
    if metadata.assets_dir is not None and has_assets:
        renames.append((metadata.assets_dir, assets_dir_for_stem(dst_stem)))

    _check_destination_parents(workspace_dir, dst_stem)
    # A destination occupied by the entry itself is a case-only rename on a
    # case-insensitive filesystem, which a plain rename handles fine.
    same_entry = _is_same_file(workspace_dir / dst_description, src_description_full)
    # entry_exists takes a reference, so hand it the description path: a raw
    # dotted stem would be re-stemmed and the wrong entry checked.
    if not same_entry and entry_exists(workspace_dir, dst_description):
        raise HTTPException(status_code=409, detail="Destination already exists")
    for source, target in renames:
        if _is_blocked_by_other(workspace_dir / target, workspace_dir / source):
            raise HTTPException(
                status_code=409, detail=f"Destination already exists: {target}"
            )

    for source, target in renames:
        target_path = workspace_dir / target
        target_path.parent.mkdir(parents=True, exist_ok=True)
        (workspace_dir / source).rename(target_path)

    src_name = PurePosixPath(src_stem).name
    dst_name = PurePosixPath(dst_stem).name
    if has_assets and src_name != dst_name:
        # The markdown references its assets as ``<stem>.assets/...``; rewrite
        # those references when the stem's basename changed.
        description_full = workspace_dir / dst_description
        body = description_full.read_text(encoding="utf-8")
        body = body.replace(f"{src_name}.assets/", f"{dst_name}.assets/")
        description_full.write_text(body, encoding="utf-8")

    # Move exactly this entry's row; a same-named sibling directory's rows
    # (stems below ``src_stem/``) belong to other documents and stay put.
    await db_documents.move_document(store, src_stem, dst_stem)
    # The described/extracted asset children live under the ``.assets`` sibling
    # of ``src_stem`` — move their rows too so nothing stays searchable at a
    # path that no longer exists.
    if metadata.assets_dir:
        await db_documents.move_subtree(
            store, metadata.assets_dir, assets_dir_for_stem(dst_stem)
        )

    return MoveDocumentResponse(
        source=src,
        destination=dst_description,
        message="Document moved successfully",
    )


async def move_document(store: Casebase, src: str, dst: str) -> MoveDocumentResponse:
    """Move a logical entry, its original, and its child-assets subtree.

    The FS renames + SQL move run to completion under the lock even on a cancel
    (:func:`shield_to_completion`) so a mid-move cancellation cannot leave the
    renamed files pointing at stale SQL rows.
    """
    async with _locked_for(store, src):
        return await shield_to_completion(_move_document_locked(store, src, dst))


def _resolve_asset_path(assets_path: Path, asset_name: str) -> tuple[str, Path]:
    try:
        safe_name = sanitize_document_path(asset_name)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    if PurePosixPath(safe_name).name != safe_name:
        raise HTTPException(status_code=400, detail="Asset name must be a filename")

    asset_path = assets_path / safe_name
    if not asset_path.resolve().is_relative_to(assets_path.resolve()):
        raise HTTPException(
            status_code=400,
            detail="Asset path escapes assets directory",
        )
    return safe_name, asset_path


def _resolve_asset_for_description(
    store: Casebase, safe: str, asset_name: str
) -> tuple[Path, str, Path]:
    """Resolve the workspace root and validated path of an asset.

    Returns ``(workspace, safe_name, asset_path)``. The caller holds the store
    lock and raises the 404 when the asset file itself is absent.
    """
    workspace = store.workspace_dir(settings.data_dir)
    assets_dir = assets_dir_for_stem(stem_path_from_reference(safe))
    assets_path = workspace / assets_dir
    safe_name, asset_path = _resolve_asset_path(assets_path, asset_name)
    return workspace, safe_name, asset_path


def _asset_entry(
    workspace: Path,
    asset_path: Path,
    safe_name: str,
    size_bytes: int,
    description: str,
    description_path: str | None,
) -> AssetEntry:
    """Build an :class:`AssetEntry` from a resolved asset path."""
    return AssetEntry(
        name=safe_name,
        path=str(asset_path.relative_to(workspace).as_posix()),
        description_path=description_path,
        description=description,
        size_bytes=size_bytes,
        media_type=mimetypes.guess_type(safe_name)[0],
    )


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
    await chunk_and_index_document(
        store, description_path, content, stat=ContentStat.from_path(md_path)
    )
    return _asset_entry(
        workspace, asset_path, safe_name, size_bytes, content, description_path
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
        workspace, safe_name, asset_path = _resolve_asset_for_description(
            store, safe, asset_name
        )
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
        workspace, safe_name, asset_path = _resolve_asset_for_description(
            store, safe, asset_name
        )
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
            assets_dir = assets_dir_for_stem(stem_path_from_reference(safe))
            windows = image_context_windows(parent_md.read_text(encoding="utf-8"))
            contexts.extend(windows.get(asset_ref_for(assets_dir, safe_name), []))
        description = await _build_image_description(
            safe_name, content_bytes, media_type, contexts, llm
        )
        return await _persist_asset_description(
            store, workspace, asset_path, safe_name, description, len(content_bytes)
        )


async def delete_asset_description(
    store: Casebase,
    safe: str,
    asset_name: str,
) -> AssetEntry:
    """Delete the companion ``.md`` description of an asset.

    The inverse of :func:`update_asset_description`: it removes the
    description file and its chunk rows while leaving the asset itself in
    place, and returns the asset entry with its description cleared. The file
    and SQL removal runs to completion under the lock even on a cancel
    (:func:`shield_to_completion`) so it cannot strand the file without its
    rows or vice versa.
    """
    async with store_lock(store):
        workspace, safe_name, asset_path = _resolve_asset_for_description(
            store, safe, asset_name
        )
        try:
            size_bytes = asset_path.stat().st_size
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Asset file not found") from exc

        md_path = asset_path.with_suffix(DOCUMENT_EXTENSION)
        description_path = str(md_path.relative_to(workspace).as_posix())
        await shield_to_completion(
            _clear_asset_description_locked(store, md_path, description_path)
        )
        return _asset_entry(workspace, asset_path, safe_name, size_bytes, "", None)


async def _clear_asset_description_locked(
    store: Casebase, md_path: Path, description_path: str
) -> None:
    """Remove a companion ``.md`` description file and its chunk rows.

    Caller holds the store lock. Tolerates a missing file and absent rows so
    clearing an asset that was only stored (never described) is a no-op.
    """
    md_path.unlink(missing_ok=True)
    await _delete_chunked_document(store, description_path)


async def create_directory(store: Casebase, path: str) -> None:
    """Create an empty workspace directory."""
    if not path:
        raise HTTPException(status_code=400, detail="Directory path required")
    _check_not_assets_path(path)
    async with store_lock(store):
        workspace_dir = store.workspace_dir(settings.data_dir)
        directory_path = workspace_dir / path
        if directory_path.exists():
            raise HTTPException(status_code=409, detail="Path already exists")
        _check_destination_parents(workspace_dir, path)
        directory_path.mkdir(parents=True, exist_ok=True)


async def _move_directory_locked(
    store: Casebase, src: str, dst: str
) -> MoveDirectoryResponse:
    """Move a directory's files and SQL rows. Caller holds the lock."""
    workspace_dir = store.workspace_dir(settings.data_dir)

    if not src:
        raise HTTPException(status_code=400, detail="Directory path required")
    _check_not_assets_path(src)
    src_dir = workspace_dir / src
    if not src_dir.is_dir():
        raise HTTPException(status_code=404, detail="Directory not found")

    dst = _resolve_move_destination(
        workspace_dir, PurePosixPath(src).name, dst, src_dir
    )
    _check_not_assets_path(dst)
    if dst == src:
        raise HTTPException(
            status_code=400, detail="Source and destination are the same"
        )
    dst_dir = workspace_dir / dst
    # Reject moving a directory beneath itself.  Inode comparison against the
    # destination's existing ancestors also catches case-aliased spellings on
    # a case-insensitive filesystem, where a string prefix check would not.
    for ancestor in dst_dir.parents:
        if ancestor == workspace_dir:
            break
        if _is_same_file(ancestor, src_dir):
            raise HTTPException(
                status_code=400, detail="Cannot move a directory into itself"
            )
    _check_destination_parents(workspace_dir, dst)
    if _is_blocked_by_other(dst_dir, src_dir):
        raise HTTPException(status_code=409, detail="Destination already exists")

    files_moved = sum(1 for file_path in src_dir.rglob("*") if file_path.is_file())
    dst_dir.parent.mkdir(parents=True, exist_ok=True)
    src_dir.rename(dst_dir)

    # Children-only: a same-named sibling document (stem equal to ``src``)
    # lives outside the directory and keeps its row.
    await db_documents.move_subtree(store, src, dst)

    return MoveDirectoryResponse(
        source=src,
        destination=dst,
        files_moved=files_moved,
        message="Directory moved successfully",
    )


async def prune_empty_dirs(store: Casebase, sources: Iterable[str]) -> None:
    """Remove directories left empty after their entries moved away.

    *sources* are the workspace-relative paths of moved entries; every
    ancestor directory of each is a candidate.  Removal uses non-recursive
    ``rmdir`` deepest-first, so a directory still holding anything — even
    content invisible to the directory tree — survives untouched.
    """
    candidates = {
        str(ancestor)
        for source in sources
        for ancestor in PurePosixPath(source).parents
        if str(ancestor) != "."
    }
    async with store_lock(store):
        workspace_dir = store.workspace_dir(settings.data_dir)
        for rel in sorted(candidates, key=lambda p: p.count("/"), reverse=True):
            try:
                (workspace_dir / rel).rmdir()
            except OSError:
                continue


async def move_directory(store: Casebase, src: str, dst: str) -> MoveDirectoryResponse:
    """Move or rename a workspace directory; document rows follow via SQL.

    The FS move + SQL move run to completion under the lock even on a cancel
    (:func:`shield_to_completion`) so the directory and its rows cannot drift
    apart.
    """
    async with _locked_for(store, scope=src):
        return await shield_to_completion(_move_directory_locked(store, src, dst))


async def _delete_directory_locked(store: Casebase, path: str) -> int:
    """Delete a directory's files and SQL rows. Caller holds the lock."""
    if not path:
        # A bare scope root resolves to an empty path; deleting it here would
        # wipe the workspace files while leaving every SQL row behind.  The
        # full wipe (files + rows) is `delete_all`.
        raise HTTPException(status_code=400, detail="Directory path required")
    workspace_dir = store.workspace_dir(settings.data_dir)
    directory_path = workspace_dir / path
    if not directory_path.is_dir():
        raise HTTPException(status_code=404, detail="Directory not found")
    files_deleted = sum(
        1 for file_path in directory_path.rglob("*") if file_path.is_file()
    )
    shutil.rmtree(directory_path)
    # Children-only: a same-named sibling document (stem equal to *path*)
    # lives outside the directory and keeps its row.
    await db_documents.delete_subtree(store, path)
    return files_deleted


async def delete_directory(store: Casebase, path: str) -> int:
    """Delete a workspace directory; matching SQL documents cascade out.

    The FS removal + SQL delete run to completion under the lock even on a
    cancel (:func:`shield_to_completion`) so the directory cannot vanish while
    its rows linger.
    """
    async with _locked_for(store, scope=path):
        return await shield_to_completion(_delete_directory_locked(store, path))


async def delete_all(store: Casebase) -> None:
    """Wipe every trace of a casebase: workspace files + SQL rows.

    Chunks cascade-delete with the documents.
    """
    async with _locked_for(store, whole_store=True):
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
    archive_path: Path,
    spec: PipelineSpec,
    llm: LlmConfig,
) -> AsyncGenerator[CollectionProgressEvent | CollectionCompleteEvent]:
    """Process a ZIP collection and yield progress events for each file.

    Each file flows through the phased :func:`upload`, which holds the casebase
    lock only for its brief reserve and commit — so the rest of the workspace
    stays responsive while a large collection processes, and a cancel mid-run
    rolls back only the in-flight file while earlier files survive.  The whole
    import holds a store claim (:func:`_store_claim`) so a concurrent store-wide
    delete or directory move cannot interleave between files and strip entries
    the collection already committed.
    """
    failed: list[str] = []
    markdown_count = 0
    converted_count = 0
    current = 0

    with _store_claim(store), tempfile.TemporaryDirectory() as tmp_dir:
        extract_root = Path(tmp_dir)

        try:
            with zipfile.ZipFile(archive_path) as archive:
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
                    text = (extract_root / relative_path).read_text(encoding="utf-8")
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
                    async with store_lock(store):
                        async with _rollback_on_failure(store, (safe,)):
                            original_bytes = (extract_root / relative_path).read_bytes()
                            original_path = workspace_dir / safe
                            original_path.parent.mkdir(parents=True, exist_ok=True)
                            original_path.write_bytes(original_bytes)
                            # The owning markdown sorts before its companion and
                            # so is already indexed with no original linked; fold
                            # the just-written original into its SQL row so delete,
                            # move, and reconvert see it without waiting for a boot.
                            await _sync_entry_from_disk_locked(store, safe)
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
                await upload(store, safe, content_bytes, spec=spec, llm=llm, origin="collection")
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
