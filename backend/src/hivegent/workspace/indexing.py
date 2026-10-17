"""The disk to SQL entry sync.

The idempotent ingest that re-derives one entry's SQL state from its on-disk
bytes.  It runs its chunk + embed + upsert step to completion under a cancel so
the workspace markdown is never left without its SQL rows.

This module owns the chunk/index primitives (:func:`chunk_and_index_document`
and :func:`delete_chunked_document`), which sibling modules import directly.
"""

import asyncio
import logging
from collections.abc import Iterable, Mapping
from pathlib import Path, PurePosixPath
from types import MappingProxyType

from ..chunks import chunk_and_index_document
from ..chunks import delete_document as delete_chunked_document
from ..concurrency import shield_to_completion
from ..config import content_digest, settings
from ..converters.plain_text import convert_plain_text
from ..db import documents as db_documents
from ..entries import (
    ContentStat,
    EntryPaths,
    is_projectable_original,
    resolve_entry_paths,
)
from ..store import Casebase
from ..text import NOT_TEXT_REASON, read_text_file
from .locks import _locked_for
from .metadata import _entry_metadata_from_disk, _refresh_unchanged_entry
from .paths import _write_markdown_file

__all__ = [
    "sync_entries_from_disk",
]

logger = logging.getLogger(__name__)


def _sync_verbatim_description(
    workspace_dir: Path, original: str, description_path: str
) -> bool:
    """Make *description_path* match *original* when its bytes are text."""
    original_full = workspace_dir / original
    # A file past the upload limit could not become an entry by any other route
    # either, so reject it on its stat rather than pulling it all into memory to
    # find out — this runs over every candidate on every boot.
    if original_full.stat().st_size > settings.limits.max_file_size_bytes:
        logger.warning("Skipping %s: too large to project", original)
        return False

    result = convert_plain_text(
        original_full.read_bytes(), PurePosixPath(original).suffix
    )
    if result is None:
        return False

    description_full = workspace_dir / description_path
    if description_full.is_file():
        current = read_text_file(description_full)
        if current is not None and current.text == result.markdown:
            return True

    _write_markdown_file(workspace_dir, description_path, result.markdown)
    logger.info("Derived %s from %s", description_path, original)
    return True


def _probe(
    workspace_dir: Path, reference: str
) -> tuple[EntryPaths, ContentStat | None]:
    """Resolve an entry, derive a projectable original's description, and stat the description.

    Blocking.  Anything but a projectable original, a binary or a format
    needing a converter or vision model, is left alone.  The stat is ``None``
    once the description is gone.
    """
    resolved = resolve_entry_paths(workspace_dir, reference)
    original = resolved.original_path

    if original is not None and is_projectable_original(original):
        _sync_verbatim_description(workspace_dir, original, resolved.description_path)

    description = workspace_dir / resolved.description_path

    return resolved, ContentStat.from_path(
        description
    ) if description.is_file() else None


async def _sync_entry_from_disk_locked(
    store: Casebase, reference: str, states: Mapping[str, db_documents.EntryState]
) -> bool:
    """Re-derive one logical entry's SQL + chunk rows from its on-disk markdown.

    The single idempotent ingest path: derive the description of a plain-text
    original that has none; drop the row if the description is gone and cannot
    be derived; skip an untouched description via a cheap ``(mtime, size)``
    stat fast-path; re-stamp metadata/stat when only companion files or the stat
    moved; chunk, embed, and upsert only when the content digest actually
    changed.  An entry with no prior SQL row is stamped ``origin="imported"``
    since its provenance cannot be recovered from disk, an existing entry keeps
    its stored provenance.  Returns whether SQL changed.  Caller must hold the
    casebase lock.
    """
    workspace_dir = store.workspace_dir(settings.data_dir)
    resolved, stat = await asyncio.to_thread(_probe, workspace_dir, reference)
    description = resolved.description_path

    if stat is None:
        # Entry gone on disk: drop the row if one exists (chunks cascade).
        return await delete_chunked_document(store, description)

    state = (
        states[description]
        if description in states
        else await db_documents.get_entry_state(store, description)
    )
    existing = state.metadata if state else None
    entry_metadata = _entry_metadata_from_disk(resolved, existing)

    # Fast path: a stored stat equal to the file's stat means the digest cannot
    # have changed, so skip the read + hash and only reconcile companion-file
    # metadata.  The stat is only ever stamped together with a digest, so its
    # presence implies an indexed row.
    if (
        state is not None
        and state.content_digest is not None
        and state.content_stat == stat
    ):
        return await _refresh_unchanged_entry(store, state, entry_metadata, stat)

    # Offloaded: reconciliation walks every description in the workspace under
    # the store lock, and a legacy-encoded one costs a detection pass.
    decoded = await asyncio.to_thread(read_text_file, workspace_dir / description)
    if decoded is None:
        # A description that is not text at all cannot be chunked; leave any
        # existing row untouched rather than replacing it with garbage.
        logger.warning("Skipping %s: content %s", description, NOT_TEXT_REASON)
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
            description,
            content,
            stat=stat,
            entry_metadata=entry_metadata,
        )
    )
    return True


async def sync_entries_from_disk(
    store: Casebase,
    references: Iterable[str],
    states: Mapping[str, db_documents.EntryState] = MappingProxyType({}),
) -> int:
    """Fold a batch of on-disk entry changes into SQL under one lock.

    The fold-back primitive a future read-write shell tool calls once a
    session ends: pass every touched description path and SQL is re-derived
    from the current bytes in a single locked, idempotent pass.  Reused by the
    startup reconciler, which passes every entry on disk and every SQL row, so
    a row whose description is gone is dropped here too, and the state of every
    row it read in one query as *states*, by description path.  An entry absent
    from *states* is looked up on its own, so one created since is not taken for
    new.  Returns the number of entries whose index changed.

    Each entry is isolated: one file that fails to index (a chunker or embedder
    error, unreadable bytes) is logged and skipped so the rest of the batch
    still reconciles.  Entries share the lock but not a transaction — each write
    commits on its own session — so skipping a failure cannot corrupt the ones
    that follow, and a single poison file can no longer strand every entry after
    it with no SQL row.
    """
    pending = tuple(references)
    async with _locked_for(store, *pending):
        changed = 0
        for reference in pending:
            try:
                if await _sync_entry_from_disk_locked(store, reference, states):
                    changed += 1
            except Exception:
                logger.warning(
                    "Failed to reconcile %s/%s from disk",
                    store.store_key,
                    reference,
                    exc_info=True,
                )
        return changed
