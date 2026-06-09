"""Document + chunk repository.

A single ``chunks`` table holds chunk metadata, text, and vectors.
``upsert_document`` writes the document row (returning a freshly-loaded
:class:`DocumentMetadata`); the chunk rows are written via cbrkit in
:func:`hivegent.retrieval.index_document` immediately after.  Chunks
cascade-delete with their owning document, so deletes only need to
touch ``documents``.
"""

from collections.abc import Sequence
from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Self

import sqlalchemy as sa
from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from ._common import affected_rows, stem_subtree_filter
from .groups import ensure_group
from .users import ensure_user

from ..chunkers.base import (
    ChunkData,
    DocumentMetadata,
    EntryMetadata,
)
from ..config import settings
from ..entries import (
    ContentStat,
    assets_dir_for_stem,
    description_path_for_stem,
    original_path_for_stem,
    stem_path_from_reference,
)
from ..store import Casebase
from .engine import session
from .models import (
    Chunk,
    Document,
    EntryKind,
    GeneratedBy,
    Origin,
)

__all__ = [
    "delete_all",
    "delete_all_documents",
    "delete_document",
    "delete_documents",
    "EntryState",
    "delete_subtree",
    "get_document",
    "get_entry_state",
    "list_document_paths",
    "list_known_stores",
    "move_document",
    "move_subtree",
    "resolve_accessible_document_ids",
    "set_content_state",
    "update_entry",
    "upsert_document",
]


@dataclass(slots=True, frozen=True)
class EntryState:
    """An entry's persisted drift fingerprint, stat, and metadata, without chunks."""

    content_digest: str | None
    content_stat: ContentStat | None
    metadata: EntryMetadata


def _stat_columns(stat: ContentStat | None) -> dict[str, int | None]:
    """Map a stat fingerprint, or its absence, onto its two nullable columns.

    The ``(content_mtime_ns, content_size)`` pair is always written and nulled
    together, so the ``None``-collapse lives here instead of at every writer.
    """
    return {
        "content_mtime_ns": stat.mtime_ns if stat else None,
        "content_size": stat.size if stat else None,
    }


def _stat_from_row(row: Document) -> ContentStat | None:
    """Rebuild the stat fingerprint persisted across the two stat columns."""
    if row.content_mtime_ns is None or row.content_size is None:
        return None
    return ContentStat(mtime_ns=row.content_mtime_ns, size=row.content_size)


# ─── Filters ───────────────────────────────────────────────────────────


def _owner_filter(store: Casebase):
    """Build a SQL WHERE expression matching *store*'s documents."""
    if store.kind == "user":
        return Document.owner_user_id == store.id
    return Document.owner_group_id == store.id


def _owner_kwargs(store: Casebase) -> dict[str, str | None]:
    """Single-owner FK kwargs for inserts."""
    if store.kind == "user":
        return {"owner_user_id": store.id, "owner_group_id": None}
    return {"owner_user_id": None, "owner_group_id": store.id}


# ─── Row → Pydantic ────────────────────────────────────────────────────


def _walk_assets(workspace_root: Path, assets_dir: str) -> list[str]:
    """Recursively enumerate workspace-relative files beneath an assets dir."""
    base = workspace_root / assets_dir
    if not base.is_dir():
        return []
    return sorted(
        str(p.relative_to(workspace_root).as_posix())
        for p in base.rglob("*")
        if p.is_file()
    )


def _entry_from_row(doc: Document, workspace_root: Path | None = None) -> EntryMetadata:
    """Build an :class:`EntryMetadata` from a row, deriving the path columns."""
    description_path = description_path_for_stem(doc.stem_path)
    original_path = original_path_for_stem(doc.stem_path, doc.original_suffix)
    assets_dir = assets_dir_for_stem(doc.stem_path) if doc.has_assets else None

    files = [description_path]
    if original_path is not None:
        files.append(original_path)
    if assets_dir is not None and workspace_root is not None:
        files.extend(_walk_assets(workspace_root, assets_dir))

    return EntryMetadata(
        entry_kind=doc.entry_kind.value,  # type: ignore[arg-type]
        stem_path=doc.stem_path,
        description_path=description_path,
        original_path=original_path,
        assets_dir=assets_dir,
        mime=doc.mime,
        origin=doc.origin.value,  # type: ignore[arg-type]
        generated_by=doc.generated_by.value,  # type: ignore[arg-type]
        files=files,
    )


def _document_from_row(
    doc: Document,
    chunks_data: Sequence[ChunkData],
    workspace_root: Path | None = None,
) -> DocumentMetadata:
    """Assemble a :class:`DocumentMetadata` from a row + prepared chunks."""
    entry = _entry_from_row(doc, workspace_root)
    return DocumentMetadata(
        **entry.model_dump(),
        id=doc.id,
        pipeline=doc.pipeline,
        created_at=doc.created_at,
        chunks=list(chunks_data),
        content_digest=doc.content_digest,
    )


async def _load_chunks(s: AsyncSession, doc_id: str) -> list[ChunkData]:
    """Load a document's chunks in idx order, reading text from ``chunks.text``."""
    stmt = (
        select(
            Chunk.idx,
            Chunk.text,
            Chunk.token_count,
            Chunk.start_index,
            Chunk.end_index,
            Chunk.start_line,
            Chunk.end_line,
        )
        .where(Chunk.document_id == doc_id)
        .order_by(Chunk.idx)
    )
    rows = (await s.execute(stmt)).all()
    return [
        ChunkData(
            text=text,
            token_count=token_count,
            start_index=start_index,
            end_index=end_index,
            start_line=start_line,
            end_line=end_line,
            index=idx,
        )
        for idx, text, token_count, start_index, end_index, start_line, end_line in rows
    ]


# ─── Helpers ──────────────────────────────────────────────────────────


def _original_suffix(original_path: str | None) -> str | None:
    """Return the pathlib suffix (with its leading dot) of *original_path*.

    ``None`` means there is no original file; an empty string means the original
    has no extension (its path is the bare stem) and must stay distinct from
    ``None`` so extension-less and dotfile originals survive the round-trip
    through :func:`original_path_for_stem`.
    """
    return PurePosixPath(original_path).suffix if original_path else None


async def _ensure_owner(s: AsyncSession, store: Casebase) -> None:
    """Lazily materialize the owning user/group row before an insert."""
    if store.kind == "user":
        await ensure_user(s, store.id)
    else:
        await ensure_group(s, store.id)


async def _find(s: AsyncSession, store: Casebase, stem_path: str) -> Document | None:
    stmt = select(Document).where(_owner_filter(store), Document.stem_path == stem_path)
    return (await s.execute(stmt)).scalar_one_or_none()


# ─── Reads ─────────────────────────────────────────────────────────────


async def get_document(store: Casebase, reference: str) -> DocumentMetadata | None:
    """Load a document and its chunks by workspace-relative reference."""
    stem_path = stem_path_from_reference(reference)
    async with session() as s:
        row = await _find(s, store, stem_path)
        if row is None:
            return None
        chunks_data = await _load_chunks(s, row.id)
        workspace_root = store.workspace_path(settings.data_dir)
        return _document_from_row(row, chunks_data, workspace_root)


async def get_entry_state(store: Casebase, reference: str) -> EntryState | None:
    """Return an entry's drift fingerprint and metadata, or ``None`` if absent.

    One row fetch for the reconcile/sync path, which needs the stored drift
    fingerprint, the stat fast-path key, and the persisted metadata without
    loading chunks.  The digest and stat are ``None`` for a row whose content
    was never indexed durably (see :func:`set_content_state`), which the caller
    treats as "needs re-indexing".
    """
    stem_path = stem_path_from_reference(reference)
    async with session() as s:
        row = await _find(s, store, stem_path)
        if row is None:
            return None
        return EntryState(
            content_digest=row.content_digest,
            content_stat=_stat_from_row(row),
            metadata=_entry_from_row(row),
        )


async def list_document_paths(store: Casebase) -> dict[str, int]:
    """Return ``{description_path: chunk_count}`` for every doc in *store*."""
    async with session() as s:
        rows = (
            await s.execute(
                select(Document.stem_path, func.count(Chunk.idx))
                .select_from(Document)
                .outerjoin(Chunk, Chunk.document_id == Document.id)
                .where(_owner_filter(store))
                .group_by(Document.id, Document.stem_path)
            )
        ).all()
    return {description_path_for_stem(stem): int(count) for stem, count in rows}


async def _distinct_owner_ids(s: AsyncSession, column) -> list[str]:
    result = await s.execute(select(column).where(column.is_not(None)).distinct())
    return [oid for oid in result.scalars().all() if oid]


async def list_known_stores() -> set[Casebase]:
    """Return every casebase that owns at least one document row.

    Ids that can no longer back a casebase (e.g. the admin marker) are
    skipped so a single legacy row cannot break reconciliation.
    """
    async with session() as s:
        user_ids = await _distinct_owner_ids(s, Document.owner_user_id)
        group_ids = await _distinct_owner_ids(s, Document.owner_group_id)
    stores: set[Casebase] = {Casebase.for_user(uid) for uid in user_ids}
    for gid in group_ids:
        try:
            stores.add(Casebase.for_group(gid))
        except ValueError:
            continue
    return stores


async def resolve_accessible_document_ids(stores: Sequence[Casebase]) -> list[str]:
    """Return every document id owned by any of *stores*.

    Used at search time to scope the cbrkit vector query without
    denormalising owner columns onto the chunks table.
    """
    if not stores:
        return []
    user_ids = [s.id for s in stores if s.kind == "user"]
    group_ids = [s.id for s in stores if s.kind == "group"]
    conditions: list[sa.ColumnElement[bool]] = []
    if user_ids:
        conditions.append(Document.owner_user_id.in_(user_ids))
    if group_ids:
        conditions.append(Document.owner_group_id.in_(group_ids))
    if not conditions:
        return []
    async with session() as s:
        result = await s.execute(select(Document.id).where(sa.or_(*conditions)))
    return list(result.scalars().all())


# ─── Writes ────────────────────────────────────────────────────────────


@dataclass(slots=True, frozen=True)
class _EntryColumns:
    """The mutable document columns an entry's metadata maps to.

    Defined and typed in one place so :func:`upsert_document` and
    :func:`update_entry` cannot drift, and a column rename or retype
    surfaces as a type error rather than a silent dict-key mismatch.
    """

    original_suffix: str | None
    has_assets: bool
    entry_kind: EntryKind
    origin: Origin
    generated_by: GeneratedBy
    mime: str | None

    @classmethod
    def from_entry(cls, entry: EntryMetadata) -> Self:
        """Derive the column values from an entry's metadata."""
        return cls(
            original_suffix=_original_suffix(entry.original_path),
            has_assets=entry.assets_dir is not None,
            entry_kind=EntryKind(entry.entry_kind),
            origin=Origin(entry.origin),
            generated_by=GeneratedBy(entry.generated_by),
            mime=entry.mime,
        )

    def as_values(self) -> dict[str, Any]:
        """Return the column mapping for a SQLAlchemy ``.values()`` / ``set_``."""
        return asdict(self)


async def upsert_document(
    store: Casebase,
    entry: EntryMetadata,
    pipeline: str,
) -> DocumentMetadata:
    """Insert or replace the SQL row for a document before indexing chunks.

    Chunk rows are written separately by
    :func:`hivegent.retrieval.index_document` so the embedding +
    INSERT happen inside cbrkit's transaction.  The returned
    :class:`DocumentMetadata` carries an empty ``chunks`` list; the
    orchestrator fills it after calling the indexer.

    The row's ``content_digest`` and stat columns are cleared here because the
    replacement chunks are not durable yet.  :func:`set_content_state` stamps
    them only after indexing succeeds, so a null digest always means "needs
    re-indexing".

    Concurrency-safe ``INSERT ... ON CONFLICT DO UPDATE`` keyed on the
    ``(owner, stem_path)`` unique constraint: two requests racing to
    index the same stem cannot trip the constraint and roll back the
    transaction (losing the document).
    """
    mutable = {
        **_EntryColumns.from_entry(entry).as_values(),
        "pipeline": pipeline,
        "content_digest": None,
        **_stat_columns(None),
    }
    owner_col = (
        Document.owner_user_id if store.kind == "user" else Document.owner_group_id
    )
    async with session() as s:
        await _ensure_owner(s, store)
        stmt = (
            pg_insert(Document)
            .values(**_owner_kwargs(store), stem_path=entry.stem_path, **mutable)
            .on_conflict_do_update(
                index_elements=[owner_col, Document.stem_path],
                set_={**mutable, "updated_at": func.now()},
            )
            .returning(Document)
        )
        doc = (await s.scalars(stmt)).one()
        workspace_root = store.workspace_path(settings.data_dir)
        return _document_from_row(doc, [], workspace_root)


async def update_entry(
    store: Casebase, entry: EntryMetadata, stat: ContentStat | None
) -> bool:
    """Refresh a document's entry metadata + stat fast-path key, leaving chunks.

    Called only when the content digest is unchanged, so the digest column is
    left untouched; this re-stamps the metadata columns (companion originals
    and assets can change without the markdown changing) and the stat so a
    later boot hits the fast path.
    """
    columns = _EntryColumns.from_entry(entry).as_values()
    async with session() as s:
        result = await s.execute(
            update(Document)
            .where(_owner_filter(store), Document.stem_path == entry.stem_path)
            .values(**columns, **_stat_columns(stat), updated_at=func.now())
        )
    return affected_rows(result) > 0


async def set_content_state(
    document_id: str, content_digest: str, stat: ContentStat | None
) -> None:
    """Stamp a document's content fingerprint + stat after chunks are durable."""
    async with session() as s:
        await s.execute(
            update(Document)
            .where(Document.id == document_id)
            .values(
                content_digest=content_digest,
                **_stat_columns(stat),
                updated_at=func.now(),
            )
        )


async def delete_document(store: Casebase, reference: str) -> bool:
    """Delete a document and its chunks.  Returns ``True`` if one was removed."""
    stem_path = stem_path_from_reference(reference)
    async with session() as s:
        result = await s.execute(
            delete(Document).where(
                _owner_filter(store), Document.stem_path == stem_path
            )
        )
    return affected_rows(result) > 0


async def delete_documents(store: Casebase, references: Sequence[str]) -> int:
    """Delete every document in *references* in one statement.

    Cascades to chunks.  Returns the number of rows removed; batching
    avoids the N+1 of deleting one reference at a time.
    """
    stems = {stem_path_from_reference(ref) for ref in references}
    if not stems:
        return 0
    async with session() as s:
        result = await s.execute(
            delete(Document).where(_owner_filter(store), Document.stem_path.in_(stems))
        )
    return affected_rows(result)


async def delete_subtree(store: Casebase, prefix: str) -> int:
    """Delete every document whose stem_path equals *prefix* or starts with ``prefix/``."""
    if not prefix:
        return 0
    async with session() as s:
        result = await s.execute(
            delete(Document).where(_owner_filter(store), stem_subtree_filter(prefix))
        )
    return affected_rows(result)


async def delete_all(store: Casebase) -> int:
    """Delete every document owned by *store*.  Cascades to chunks."""
    async with session() as s:
        result = await s.execute(delete(Document).where(_owner_filter(store)))
    return affected_rows(result)


async def delete_all_documents() -> int:
    """Delete every document row globally.  Cascades to chunks.

    Used by the admin "reset workspace" action together with a
    filesystem wipe; the workspace and the SQL projection must move in
    lockstep, otherwise reconciliation will re-create one from the
    other on next boot.
    """
    async with session() as s:
        result = await s.execute(delete(Document))
    return affected_rows(result)


async def move_document(
    store: Casebase, src_reference: str, dst_reference: str
) -> bool:
    """Rename a document's ``stem_path``.

    Chunks reference the document by id (which never changes) so the
    vector index needs no reindex after a rename.
    """
    src_stem = stem_path_from_reference(src_reference)
    dst_stem = stem_path_from_reference(dst_reference)
    if src_stem == dst_stem:
        return False
    async with session() as s:
        result = await s.execute(
            update(Document)
            .where(_owner_filter(store), Document.stem_path == src_stem)
            .values(stem_path=dst_stem)
        )
    return affected_rows(result) > 0


async def move_subtree(store: Casebase, src_prefix: str, dst_prefix: str) -> None:
    """Rename every document under ``src_prefix`` to live under ``dst_prefix``.

    A single bulk UPDATE rewrites the ``src_prefix`` portion of each
    matched ``stem_path`` to ``dst_prefix``.  No vector reindex needed —
    chunks reference the immutable document id.
    """
    if not src_prefix or src_prefix == dst_prefix:
        return
    async with session() as s:
        await s.execute(
            update(Document)
            .where(_owner_filter(store), stem_subtree_filter(src_prefix))
            .values(
                stem_path=func.concat(
                    dst_prefix, func.substr(Document.stem_path, len(src_prefix) + 1)
                )
            )
        )
