"""Entry metadata derivation and digest-skip reconciliation.

The canonical :class:`~hivegent.chunkers.base.EntryMetadata` for a logical
entry, derived from its on-disk paths while preserving SQL-only provenance,
plus the helper that re-stamps a digest-unchanged entry without touching its
chunks.
"""

import mimetypes

from ..chunkers.base import EntryGeneratedBy, EntryKind, EntryMetadata, EntryOrigin
from ..db import documents as db_documents
from ..entries import ContentStat, EntryPaths
from ..store import Casebase

__all__: list[str] = []


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
