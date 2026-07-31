"""Markdown projection writes and the disk to SQL entry sync.

The single place that writes a markdown projection together with its chunk
and vector rows, and the idempotent ingest that re-derives one entry's SQL
state from its on-disk bytes.  Both run the chunk + embed + upsert step to
completion under a cancel so the workspace markdown is never left without
its SQL rows.

This module owns the chunk/index primitives (:func:`chunk_and_index_document`
and :func:`delete_chunked_document`), which sibling modules import directly.
"""

import asyncio
import logging
from collections.abc import Iterable

from ..chunkers.base import EntryMetadata
from ..chunks import chunk_and_index_document
from ..chunks import delete_document as delete_chunked_document
from ..concurrency import shield_to_completion
from ..config import content_digest, settings
from ..db import documents as db_documents
from ..entries import ContentStat, resolve_entry_paths
from ..store import Casebase
from ..text import NOT_TEXT_REASON, read_text_file
from ..types import PipelineSpec
from .locks import store_lock
from .metadata import _entry_metadata_from_disk, _refresh_unchanged_entry

__all__ = [
    "sync_entries_from_disk",
    "sync_entry_from_disk",
]

logger = logging.getLogger(__name__)


async def write_markdown_projection(
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
        return await delete_chunked_document(store, resolved.description_path)

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

    # Offloaded: reconciliation walks every description in the workspace under
    # the store lock, and a legacy-encoded one costs a detection pass.
    decoded = await asyncio.to_thread(read_text_file, description_full)
    if decoded is None:
        # A description that is not text at all cannot be chunked; leave any
        # existing row untouched rather than replacing it with garbage.
        logger.warning(
            "Skipping %s: content %s", resolved.description_path, NOT_TEXT_REASON
        )
        return False

    content = decoded.text
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

    Each entry is isolated: one file that fails to index (a chunker or embedder
    error, unreadable bytes) is logged and skipped so the rest of the batch
    still reconciles.  Entries share the lock but not a transaction — each write
    commits on its own session — so skipping a failure cannot corrupt the ones
    that follow, and a single poison file can no longer strand every entry after
    it with no SQL row.
    """
    async with store_lock(store):
        changed = 0
        for reference in references:
            try:
                if await _sync_entry_from_disk_locked(store, reference):
                    changed += 1
            except Exception:
                logger.warning(
                    "Failed to reconcile %s/%s from disk",
                    store.store_key,
                    reference,
                    exc_info=True,
                )
        return changed
