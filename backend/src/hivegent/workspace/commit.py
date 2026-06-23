"""Atomic commit, entry deletion, rollback, and the phased-upload lifecycle.

The only phase that mutates the workspace.  :func:`_commit_prepared` applies a
prepared upload and supersedes any prior entry in one locked, cancel-shielded
step; :func:`_phased_upload` strings the reserve, lock-free prepare, and commit
together so the casebase lock is held only for the brief reserve and commit.
"""

import logging
import shutil
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from contextlib import asynccontextmanager

from fastapi import HTTPException

# Module-object imports (absolute path) keep test seams patchable and out of a cycle.
import hivegent.workspace.indexing as indexing
import hivegent.workspace.prepare as prepare
from ..concurrency import shield_to_completion
from ..config import settings
from ..db import documents as db_documents
from ..entries import (
    assets_dir_for_stem,
    description_path_for_stem,
    entry_exists,
    resolve_entry_paths,
    stem_path_from_reference,
)
from ..store import Casebase
from ..types import LlmConfig, PipelineSpec, ProgressReporter, UploadCompleteEvent
from .locks import _add_inflight, _discard_inflight, _locked_for, store_lock
from .paths import _check_destination_parents, _write_original_file

__all__: list[str] = []

logger = logging.getLogger(__name__)


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


async def _commit_prepared(
    store: Casebase,
    prepared: prepare._PreparedUpload,
    spec: PipelineSpec,
    reserved: prepare._Reserved,
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
        await indexing.write_markdown_projection(
            store,
            entry.description_path,
            entry.markdown,
            spec,
            entry_metadata=entry.entry_metadata,
        )

    chunk_count, chunking_used = await indexing.write_markdown_projection(
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

    await indexing.delete_chunked_document(store, safe)


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


type _Reserve = Callable[[], Awaitable[prepare._Reserved]]


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
    reserved: prepare._Reserved | None = None
    try:
        async with _locked_for(store, stem_reference):
            reserved = await reserve()
            _add_inflight(store, stem_reference)
            claimed = True

        prepared = await prepare._prepare_upload(
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
