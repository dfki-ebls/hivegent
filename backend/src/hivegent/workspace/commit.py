"""Atomic commit, entry deletion, and the phased-upload lifecycle.

:func:`_write_prepared_files` is the only phase that mutates the workspace, and
the only one that needs the casebase lock; :func:`_index_prepared` then chunks,
embeds, and persists the SQL rows for what it wrote, without the lock.
:func:`_apply_prepared` pairs them, and :func:`_phased_upload` strings the
reserve, lock-free prepare, and apply together — so of an upload's four phases
only the reserve and the file writes are serialised against the rest of the
store.
"""

import asyncio
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory

from fastapi import HTTPException

from ..chunkers.base import DocumentMetadata
from ..concurrency import shield_to_completion
from ..config import settings
from ..db import documents as db_documents
from ..entries import (
    ContentStat,
    assets_dir_for_stem,
    description_path_for_stem,
    entry_exists,
    resolve_entry_paths,
    stem_path_from_reference,
)
from ..store import Casebase
from ..types import LlmConfig, PipelineSpec, ProgressReporter, UploadCompleteEvent
from .indexing import chunk_and_index_document, delete_chunked_document
from .locks import _add_inflight, _discard_inflight, _locked_for, store_lock
from .metadata import _merge_entry_paths
from .paths import (
    _check_destination_parents,
    _remove_tree,
    _replace_workspace_paths,
    _WorkspaceChange,
    _write_markdown_file,
    _write_workspace_file,
)
from .prepare import _prepare_upload, _PreparedEntry, _PreparedUpload, _Reserved

__all__: list[str] = []

logger = logging.getLogger(__name__)


@dataclass(slots=True, frozen=True)
class _WrittenEntry:
    """A markdown projection already on disk, still awaiting its SQL rows.

    Carries the fingerprint captured at write time so the index step stamps the
    stat of the bytes it actually indexed rather than re-reading it later.
    """

    entry: _PreparedEntry
    stat: ContentStat | None


async def _write_prepared_files(
    store: Casebase, prepared: _PreparedUpload, reserved: _Reserved
) -> tuple[_WrittenEntry, ...]:
    """Apply a prepared upload's bytes to the workspace. Caller holds the lock.

    This is the only phase that mutates the workspace.  It writes everything to
    workspace-local staging first, then replaces the live paths while retaining
    backups until the swap and related SQL cleanup succeed.  Any apply failure
    restores the complete prior entry.

    Returns the written projections in index order (assets first, main last), so
    the markdown that references the assets is only indexed once their own
    entries are.
    """
    workspace = store.workspace_dir(settings.data_dir)
    stem_path = stem_path_from_reference(reserved.reference)
    assets_dir = assets_dir_for_stem(stem_path)

    with TemporaryDirectory(
        prefix=f".{workspace.name}-stage-", dir=workspace.parent
    ) as tmp:
        staging = Path(tmp)
        new_root = staging / "new"
        backup_root = staging / "backup"

        if reserved.original_path and reserved.original_content is not None:
            _write_workspace_file(
                new_root, reserved.original_path, reserved.original_content
            )
        for asset in prepared.assets:
            _write_workspace_file(new_root, asset.path, asset.data)
        for entry in (*prepared.asset_entries, prepared.main):
            _write_markdown_file(new_root, entry.description_path, entry.markdown)

        has_assets = bool(prepared.assets or prepared.asset_entries)
        changes = [
            _WorkspaceChange(
                prepared.main.description_path,
                new_root / prepared.main.description_path,
            )
        ]
        if reserved.preserve or has_assets:
            changes.append(
                _WorkspaceChange(
                    assets_dir, new_root / assets_dir if has_assets else None
                )
            )
        if reserved.original_path and reserved.original_content is not None:
            changes.append(
                _WorkspaceChange(
                    reserved.original_path, new_root / reserved.original_path
                )
            )
        if (
            reserved.supersede_original is not None
            and reserved.supersede_original != reserved.original_path
        ):
            changes.append(_WorkspaceChange(reserved.supersede_original, None))

        with _replace_workspace_paths(workspace, backup_root, changes):
            if reserved.preserve:
                await db_documents.delete_subtree(store, assets_dir)

            return tuple(
                _WrittenEntry(
                    entry,
                    ContentStat.from_path(workspace / entry.description_path),
                )
                for entry in (*prepared.asset_entries, prepared.main)
            )


async def _index_entry(
    store: Casebase, written: _WrittenEntry, spec: PipelineSpec
) -> DocumentMetadata:
    """Chunk, embed, and persist the SQL rows for one on-disk projection."""
    return await chunk_and_index_document(
        store,
        written.entry.description_path,
        written.entry.markdown,
        spec.chunking,
        stat=written.stat,
        entry_metadata=written.entry.entry_metadata,
    )


async def _index_prepared(
    store: Casebase,
    prepared: _PreparedUpload,
    written: tuple[_WrittenEntry, ...],
    spec: PipelineSpec,
) -> UploadCompleteEvent:
    """Index the projections :func:`_write_prepared_files` put on disk.

    Runs *without* the casebase lock: nothing here touches the workspace, and
    the upload's in-flight claim already rejects every mutation aimed at this
    entry or its assets, so the lock buys nothing the claim does not give.

    The window in which the files exist without their rows is the one the design
    already tolerates everywhere: the entry stays hidden from inventory reads
    while its stem is in flight, and a null ``content_digest`` is the standing
    "not indexed" marker the startup reconciler repairs.
    """
    *assets, main = written
    for item in assets:
        await _index_entry(store, item, spec)
    chunked = await _index_entry(store, main, spec)

    return UploadCompleteEvent(
        filename=prepared.filename,
        converted_filename=prepared.converted_filename,
        size_bytes=prepared.size_bytes,
        conversion_pipeline_used=prepared.conversion_pipeline_used,
        chunk_count=len(chunked.chunks),
        chunking_pipeline_used=chunked.pipeline,
        message=prepared.message,
    )


async def _apply_prepared(
    store: Casebase,
    prepared: _PreparedUpload,
    spec: PipelineSpec,
    reserved: _Reserved,
) -> UploadCompleteEvent:
    """Land a prepared upload: files under the lock, index without it."""
    async with store_lock(store):
        written = await _write_prepared_files(store, prepared, reserved)

    return await _index_prepared(store, prepared, written, spec)


async def _delete_single_locked(store: Casebase, safe: str) -> None:
    """Remove a logical entry's files, metadata, and index rows.

    Works for entries without a SQL row or description file too (e.g. a
    stray original that was never ingested), so any on-disk entry can
    always be removed through the API.
    """
    workspace = store.workspace_dir(settings.data_dir)
    metadata = await db_documents.get_entry_metadata(store, safe)
    if not metadata and not entry_exists(workspace, safe):
        raise HTTPException(status_code=404, detail="Document not found")

    resolved = _merge_entry_paths(resolve_entry_paths(workspace, safe), metadata)
    (workspace / resolved.description_path).unlink(missing_ok=True)

    if resolved.original_path:
        (workspace / resolved.original_path).unlink(missing_ok=True)

    if resolved.assets_dir:
        await asyncio.to_thread(_remove_tree, workspace / resolved.assets_dir)
        await db_documents.delete_subtree(store, resolved.assets_dir)

    await delete_chunked_document(store, safe)


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


def _ensure_upload_slot_locked(
    store: Casebase, reference: str, *, overwrite: bool
) -> None:
    """Validate that *reference*'s slot can be written, raising 409 if blocked.

    Rejects a destination whose parent chain is a file, a target occupied by a
    directory, and a non-overwrite write onto an existing entry.  It performs no
    deletion: an overwrite's stale parts are superseded atomically at commit
    (see :func:`_write_prepared_files`), so a failed or cancelled conversion can
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


type _Reserve = Callable[[], Awaitable[_Reserved]]


@asynccontextmanager
async def _reserved_upload(
    store: Casebase, stem_reference: str, reserve: _Reserve
) -> AsyncIterator[_Reserved]:
    """Reserve and claim an upload until its prepare and apply phases settle."""
    async with AsyncExitStack() as claim:
        async with _locked_for(store, stem_reference):
            reserved = await reserve()
            claim.callback(_discard_inflight, store, stem_reference)
            _add_inflight(store, stem_reference)

        yield reserved


async def _phased_upload(
    store: Casebase,
    spec: PipelineSpec,
    llm: LlmConfig,
    *,
    stem_reference: str,
    reserve: _Reserve,
    ctx: ProgressReporter | None,
) -> UploadCompleteEvent:
    """Run an upload's reserve → prepare → apply phases.

    The lock is held only for the brief *reserve* (validate + capture) and the
    file writes inside *apply*; the slow *prepare* (conversion, captioning) and
    the equally slow index step (chunk, embed, upsert) both run lock-free, the
    former against a temp copy of the source so it never touches the live
    workspace.  Because nothing is written until apply, a failure or
    cancellation during prepare leaves a pre-existing entry (``preserve``)
    completely intact; only a genuinely new entry is rolled back by deleting it.
    *stem_reference* is the stem this upload owns for its whole lifecycle.

    Apply is shielded as a single unit even though it releases the lock partway
    through, so a cancel can never settle between the file writes and the index
    and leave a reprocessed entry's new markdown wearing its predecessor's rows.
    """
    async with _reserved_upload(store, stem_reference, reserve) as reserved:
        try:
            prepared = await _prepare_upload(
                store,
                reserved.reference,
                reserved.content,
                spec,
                llm,
                origin=reserved.origin,
                original_path=reserved.original_path,
                ctx=ctx,
                clearing_assets=reserved.preserve,
            )
            return await shield_to_completion(
                _apply_prepared(store, prepared, spec, reserved)
            )
        except BaseException:
            # A new entry's partial artifacts are rolled back; a pre-existing entry
            # is left untouched (prepare never wrote into the workspace).
            if not reserved.preserve:
                async with store_lock(store):
                    await shield_to_completion(
                        _safe_delete_locked(store, reserved.reference)
                    )
            raise
