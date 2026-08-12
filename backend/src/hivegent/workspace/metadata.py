"""Entry metadata derivation and digest-skip reconciliation.

The canonical :class:`~hivegent.chunkers.base.EntryMetadata` for a logical
entry, derived from its on-disk paths while preserving SQL-only provenance,
plus the helper that re-stamps a digest-unchanged entry without touching its
chunks.
"""

import mimetypes

from ..chunkers.base import EntryGeneratedBy, EntryKind, EntryMetadata, EntryOrigin
from ..config import settings
from ..converters import EntryProjection, projection_for
from ..db import documents as db_documents
from ..entries import ContentStat, EntryPaths, resolve_entry_paths
from ..store import Casebase

__all__ = ["resolve_entry"]


def _merge_entry_paths(
    resolved: EntryPaths, metadata: EntryMetadata | None
) -> EntryPaths:
    """Overlay an entry's recorded SQL paths onto the ones discovered on disk.

    The SQL row wins for every companion file it names, since it knows which
    original and assets directory belong to the entry; disk resolution fills in
    whatever the row leaves unset, so a stray original beside an indexed
    description is still treated as part of the entry — exactly as the inventory
    reads already group it.
    """
    if metadata is None:
        return resolved

    return EntryPaths(
        stem_path=metadata.stem_path or resolved.stem_path,
        description_path=metadata.description_path or resolved.description_path,
        original_path=metadata.original_path or resolved.original_path,
        assets_dir=metadata.assets_dir or resolved.assets_dir,
    )


async def resolve_entry(store: Casebase, reference: str) -> EntryPaths:
    """Resolve a logical entry's paths from disk, overlaid with its SQL row."""
    return _merge_entry_paths(
        resolve_entry_paths(store.workspace_dir(settings.data_dir), reference),
        await db_documents.get_entry_metadata(store, reference),
    )


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


_REDISCOVERED_KINDS: dict[EntryProjection, tuple[EntryKind, EntryGeneratedBy]] = {
    EntryProjection.MARKDOWN: ("user_markdown", "user"),
    EntryProjection.IMAGE: ("image", "vision"),
    EntryProjection.VIDEO: ("video", "vision"),
    EntryProjection.CONVERTIBLE: ("convertible", "converter"),
}
"""What a rediscovered entry's original says its description was produced by.

The same routing :func:`~hivegent.workspace.prepare._prepare_upload` dispatches
on, so a row rebuilt from disk describes the entry the way the uploader would
have.  Getting this wrong is not cosmetic: retrieval attaches an entry's
original only when it reads back as ``image``.
"""


def _entry_metadata_from_disk(
    resolved: EntryPaths, existing: EntryMetadata | None
) -> EntryMetadata:
    """Build current disk metadata, preserving SQL-only provenance when present.

    Without a prior row the provenance is inferred from the files alone: a lone
    description is user markdown, and one sitting beside an original is that
    original's projection.  A markdown upload that carried a companion original
    is the one shape this infers wrongly, and only after its row was lost — the
    same limitation that already stamps every rediscovered entry ``imported``.
    """
    kind, generated_by = (
        _REDISCOVERED_KINDS[projection_for(resolved.original_path)]
        if resolved.original_path is not None
        else _REDISCOVERED_KINDS[EntryProjection.MARKDOWN]
    )
    return _build_entry_metadata(
        stem_path=resolved.stem_path,
        description_path=resolved.description_path,
        original_path=resolved.original_path,
        assets_dir=resolved.assets_dir,
        entry_kind=existing.entry_kind if existing else kind,
        origin=existing.origin if existing else "imported",
        generated_by=existing.generated_by if existing else generated_by,
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
