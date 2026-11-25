"""The disk to SQL entry sync.

The idempotent ingest that re-derives one entry's SQL state from its on-disk
bytes.  It runs its chunk + embed + upsert step to completion under a cancel so
the workspace markdown is never left without its SQL rows.

This module owns the chunk/index primitives (:func:`chunk_and_index_document`
and :func:`delete_chunked_document`), which sibling modules import directly.
"""

import asyncio
import logging
from collections.abc import Iterable, Mapping, Sequence
from functools import cache
from pathlib import Path
from stat import S_ISREG
from types import MappingProxyType

from exar.helpers import bounded_gather, shield_to_completion
from exar.memory import digest
from exar.text import NOT_TEXT_REASON, decode

from ..chunks import chunk_and_index_document, chunking
from ..config import settings
from ..db import documents as db_documents
from ..db.documents import delete_document as delete_chunked_document
from ..metadata import EntryMetadata
from ..pipelines import chunker_fingerprint
from ..store import Workspace
from ..tools.entries import (
    ContentStat,
    EntryPaths,
    Listdir,
    list_names,
    resolve_entry_paths,
)
from .locks import root_lock
from .metadata import _entry_metadata_from_disk, _refresh_unchanged_entry
from .operations import Root

__all__ = [
    "sync_entries_from_disk",
]

logger = logging.getLogger(__name__)


type _Probe = tuple[EntryPaths, ContentStat | None]


def _probe(workspace_dir: Path, reference: str, listdir: Listdir) -> _Probe:
    """Resolve an entry and stat its description.

    Blocking.  The stat is ``None`` once the description is gone or no file.

    Raises:
        OSError: When the description cannot be stated for any other reason,
            such as a stale or unreadable share, so its row is not taken for gone.
    """
    resolved = resolve_entry_paths(workspace_dir, reference, listdir)

    try:
        st = (workspace_dir / resolved.description_path).stat()
    except FileNotFoundError, NotADirectoryError:
        return resolved, None

    return resolved, ContentStat.of(st) if S_ISREG(st.st_mode) else None


def _probe_all(store: Workspace, references: Sequence[str]) -> dict[str, _Probe]:
    """:func:`_probe` every reference in one pass, listing each folder once.

    Blocking.  A reference that fails to probe is logged and left out.
    """
    workspace_dir = store.workspace_dir()
    listdir = cache(list_names)
    probes: dict[str, _Probe] = {}

    for reference in references:
        try:
            probes[reference] = _probe(workspace_dir, reference, listdir)
        except Exception:
            logger.warning(
                "Failed to reconcile %s/%s from disk",
                store.store_key,
                reference,
                exc_info=True,
            )

    return probes


def _chunked_currently(
    store: Workspace, state: db_documents.EntryState, entry: EntryMetadata
) -> bool:
    """Whether the row *state* was indexed, by the chunker the entry is chunked with now.

    A chunker that cannot be built, such as of a missing extra, never matches,
    so the entry's sync reports why.  An unindexed row has no chunker.
    """
    try:
        return state.chunker == chunker_fingerprint(chunking(store, entry))
    except ValueError:
        return False


def _is_current(
    store: Workspace,
    resolved: EntryPaths,
    stat: ContentStat | None,
    state: db_documents.EntryState | None,
) -> bool:
    """Whether the row *state* already matches the entry's files and chunker, so nothing is to be done."""
    if stat is None or state is None or state.content_stat != stat:
        return False

    entry = _entry_metadata_from_disk(resolved, state.metadata)

    return state.metadata == entry and _chunked_currently(store, state, entry)


async def _sync_entry_from_disk_locked(
    store: Workspace,
    resolved: EntryPaths,
    stat: ContentStat | None,
    state: db_documents.EntryState | None,
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
    workspace lock.
    """
    workspace_dir = store.workspace_dir()
    description = resolved.description_path

    if stat is None:
        # Entry gone on disk: drop the row if one exists (chunks cascade).
        return await delete_chunked_document(store, description)

    if state is None:
        state = await db_documents.get_entry_state(store, description)

    existing = state.metadata if state else None
    entry_metadata = _entry_metadata_from_disk(resolved, existing)

    # Fast path: a stored stat equal to the file's stat means the digest cannot
    # have changed, so skip the read + hash and only reconcile companion-file
    # metadata, unless the chunks were made with another chunker.
    current = (
        state
        if state is not None and _chunked_currently(store, state, entry_metadata)
        else None
    )

    if current is not None and current.content_stat == stat:
        return await _refresh_unchanged_entry(store, current, entry_metadata, stat)

    # Offloaded: reconciliation walks every description in the workspace under
    # the store lock, and a legacy-encoded one costs a detection pass.
    decoded = await asyncio.to_thread(
        lambda: decode((workspace_dir / description).read_bytes())
    )
    if decoded is None:
        # A description that is not text at all cannot be chunked; leave any
        # existing row untouched rather than replacing it with garbage.
        logger.warning("Skipping %s: content %s", description, NOT_TEXT_REASON)
        return False

    content = decoded.text
    if current is not None and current.content_digest == digest(content):
        # Content identical despite a moved stat (touch, checkout, restore):
        # persist the fresh stat so the next boot hits the fast path, plus any
        # companion-metadata drift.  No re-embed.
        return await _refresh_unchanged_entry(store, current, entry_metadata, stat)

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
    store: Workspace,
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
    still reconciles.  Entries share the lock but not a transaction.  Each write
    commits on its own session, so skipping a failure cannot corrupt the ones
    that follow, and a single poison file can no longer strand every entry after
    it with no SQL row.
    """
    pending = tuple(references)
    async with root_lock(Root.of(store)):
        probes = await asyncio.to_thread(_probe_all, store, pending)
        stale = [
            reference
            for reference, (resolved, stat) in probes.items()
            if not _is_current(
                store, resolved, stat, states.get(resolved.description_path)
            )
        ]

        async def sync(reference: str) -> bool:
            resolved, stat = probes[reference]

            try:
                return await _sync_entry_from_disk_locked(
                    store, resolved, stat, states.get(resolved.description_path)
                )
            except Exception:
                logger.warning(
                    "Failed to reconcile %s/%s from disk",
                    store.store_key,
                    reference,
                    exc_info=True,
                )

                return False

        changed = await bounded_gather(
            stale, sync, limit=settings.jobs.collection_concurrency
        )

        return sum(changed)
