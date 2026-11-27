"""Document + chunk repository.

A single ``chunks`` table holds chunk metadata, text, and vectors.
``upsert_document`` writes the document row (returning a freshly-loaded
:class:`DocumentMetadata`); the chunk rows are written via cbrkit in
:func:`hivegent.retrieval.index_document` immediately after.  Chunks
cascade-delete with their owning document, so deletes only need to
touch ``documents``.
"""

from collections.abc import Iterable, Sequence
from dataclasses import asdict, dataclass
from pathlib import PurePosixPath
from typing import Any, Self, TypedDict
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from ..metadata import ChunkData, DocumentMetadata, EntryMetadata
from ..reach import Reach
from ..shared.formats import DOCUMENT_EXTENSION, media_type
from ..store import Workspace
from ..tools.entries import (
    ContentStat,
    Part,
    stem_path_from_reference,
)
from ..types import FilterEntry
from ._common import affected_rows, stem_subtree_filter
from .engine import session
from .models import (
    Chunk,
    Document,
    EntryKind,
    GeneratedBy,
    Origin,
)
from .workspaces import ensure_workspace

__all__ = [
    "EntryState",
    "accessible_chunks",
    "accessible_documents",
    "clear_content_states",
    "delete_document",
    "delete_documents",
    "delete_subtree",
    "get_document",
    "get_entries_metadata",
    "get_entry_metadata",
    "get_entry_state",
    "get_entry_states",
    "get_line_counts",
    "list_document_paths",
    "move_document",
    "move_subtree",
    "set_content_state",
    "update_entry",
    "upsert_document",
]


@dataclass(slots=True, frozen=True)
class EntryState:
    """An entry's persisted drift fingerprint, stat, and metadata, without chunks."""

    content_digest: str | None
    chunker: str | None
    content_stat: ContentStat | None
    metadata: EntryMetadata


class _StatColumns(TypedDict):
    """The three nullable stat columns, written and nulled together."""

    content_mtime_ns: int | None
    content_size: int | None
    content_inode: int | None


def _stat_columns(stat: ContentStat | None) -> _StatColumns:
    """Map a stat fingerprint, or its absence, onto its three nullable columns.

    The columns are always written and nulled together, so the ``None``-collapse
    lives here instead of at every writer.
    """
    return {
        "content_mtime_ns": stat.mtime_ns if stat else None,
        "content_size": stat.size if stat else None,
        "content_inode": stat.inode if stat else None,
    }


def _stat_from_row(row: Document) -> ContentStat | None:
    """Rebuild the stat fingerprint persisted across the three stat columns."""
    if (
        row.content_mtime_ns is None
        or row.content_size is None
        or row.content_inode is None
    ):
        return None

    return ContentStat(
        mtime_ns=row.content_mtime_ns, size=row.content_size, inode=row.content_inode
    )


# ─── Filters ───────────────────────────────────────────────────────────


def _workspace_filter(store: Workspace) -> sa.ColumnElement[bool]:
    """Build a SQL WHERE expression matching *store*'s documents, for a read.

    Every query on ``documents`` names its rows through this or
    :func:`_writable_filter`, so no statement here reaches another workspace's.
    """
    return Document.workspace_key == store.store_key


def _writable_filter(store: Workspace) -> sa.ColumnElement[bool]:
    """Match *store*'s documents for an update or delete, refusing a grant short of writing it.

    Raises:
        AccessDenied: When *store* is not granted :attr:`~hivegent.store.Grant.WRITE`.
    """
    store.require_writable()

    return _workspace_filter(store)


def _writable_key(store: Workspace) -> str:
    """The workspace key of a row inserted or moved into *store*, refusing a grant short of writing it.

    Raises:
        AccessDenied: When *store* is not granted :attr:`~hivegent.store.Grant.WRITE`.
    """
    store.require_writable()

    return store.store_key


# ─── Row → Pydantic ────────────────────────────────────────────────────


def _entry_from_row(doc: Document) -> EntryMetadata:
    """Build an :class:`EntryMetadata` from a row, deriving the path columns."""
    return EntryMetadata(
        entry_kind=doc.entry_kind.value,  # type: ignore[arg-type]
        stem_path=doc.stem_path,
        description_path=Part(doc.stem_path, "description").path,
        original_path=None
        if doc.original_suffix is None
        else Part(doc.stem_path, "original", doc.original_suffix).path,
        assets_dir=Part(doc.stem_path, "assets").path if doc.has_assets else None,
        origin=doc.origin.value,  # type: ignore[arg-type]
        generated_by=doc.generated_by.value,  # type: ignore[arg-type]
        converter=doc.converter,
    )


def _document_from_row(
    doc: Document, chunks_data: Sequence[ChunkData]
) -> DocumentMetadata:
    """Assemble a :class:`DocumentMetadata` from a row + prepared chunks."""
    entry = _entry_from_row(doc)
    return DocumentMetadata(
        **entry.model_dump(),
        mime=media_type(entry.original_path or entry.description_path),
        id=doc.id,
        pipeline=doc.pipeline,
        created_at=doc.created_at,
        size_bytes=doc.content_size,
        chunks=list(chunks_data),
    )


async def _load_chunks(s: AsyncSession, doc_id: UUID) -> list[ChunkData]:
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
    through :class:`~hivegent.tools.entries.Part`.
    """
    return PurePosixPath(original_path).suffix if original_path else None


async def _find(s: AsyncSession, store: Workspace, stem_path: str) -> Document | None:
    stmt = select(Document).where(
        _workspace_filter(store), Document.stem_path == stem_path
    )
    return (await s.execute(stmt)).scalar_one_or_none()


# ─── Reads ─────────────────────────────────────────────────────────────


async def get_document(store: Workspace, reference: str) -> DocumentMetadata | None:
    """Load a document and its chunks by workspace-relative reference."""
    stem_path = stem_path_from_reference(reference)
    async with session() as s:
        row = await _find(s, store, stem_path)
        if row is None:
            return None
        return _document_from_row(row, await _load_chunks(s, row.id))


async def get_entry_metadata(store: Workspace, reference: str) -> EntryMetadata | None:
    """Load an entry's persisted metadata without its chunks."""
    stem_path = stem_path_from_reference(reference)
    async with session() as s:
        row = await _find(s, store, stem_path)
        return _entry_from_row(row) if row is not None else None


async def get_entries_metadata(
    references: Iterable[tuple[Workspace, str]],
) -> dict[tuple[Workspace, str], EntryMetadata]:
    """Load the persisted metadata of many entries in one query, by store and stem.

    The batch sibling of :func:`get_entry_metadata`, and an entry without a row is
    absent from the result.
    """
    stems: dict[Workspace, set[str]] = {}

    for store, reference in references:
        stems.setdefault(store, set()).add(stem_path_from_reference(reference))

    if not stems:
        return {}

    where = sa.or_(
        *(
            sa.and_(_workspace_filter(store), Document.stem_path.in_(paths))
            for store, paths in stems.items()
        )
    )

    by_key = {store.store_key: store for store in stems}

    async with session() as s:
        rows = (await s.scalars(select(Document).where(where))).all()

    return {
        (by_key[row.workspace_key], row.stem_path): _entry_from_row(row) for row in rows
    }


async def get_entry_state(store: Workspace, reference: str) -> EntryState | None:
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
        return _state_from_row(row) if row is not None else None


async def get_entry_states(store: Workspace) -> dict[str, EntryState]:
    """Return every entry's state in *store* by its description path, in one query."""
    async with session() as s:
        rows = (await s.scalars(select(Document).where(_workspace_filter(store)))).all()

    return {
        Part(row.stem_path, "description").path: _state_from_row(row) for row in rows
    }


def _state_from_row(row: Document) -> EntryState:
    return EntryState(
        content_digest=row.content_digest,
        chunker=row.chunker,
        content_stat=_stat_from_row(row),
        metadata=_entry_from_row(row),
    )


async def list_document_paths(store: Workspace) -> dict[str, int]:
    """Return ``{description_path: chunk_count}`` for every doc in *store*."""
    async with session() as s:
        rows = (
            await s.execute(
                select(Document.stem_path, func.count(Chunk.idx))
                .select_from(Document)
                .outerjoin(Chunk, Chunk.document_id == Document.id)
                .where(_workspace_filter(store))
                .group_by(Document.id, Document.stem_path)
            )
        ).all()
    return {Part(stem, "description").path: int(count) for stem, count in rows}


async def get_line_counts(
    store: Workspace, references: Sequence[str]
) -> dict[str, int]:
    """Return ``{reference: line_count}`` for the given workspace references.

    Keyed on ``line_count`` rather than the content digest: ``upsert_document``
    writes ``line_count`` eagerly (it is a pure function of the content, correct
    whether or not the chunks land) and never clears it, so an in-place re-index
    keeps reporting the length instead of blinking to "unknown".  A reference
    with no row, or a not-yet-indexed ``0`` placeholder, is simply absent so the
    caller treats it as "length unknown" and hides the map.
    """
    stem_to_ref = {stem_path_from_reference(r): r for r in references}
    if not stem_to_ref:
        return {}
    async with session() as s:
        rows = (
            await s.execute(
                select(Document.stem_path, Document.line_count).where(
                    _workspace_filter(store),
                    Document.stem_path.in_(stem_to_ref),
                    Document.line_count > 0,
                )
            )
        ).all()
    return {stem_to_ref[stem]: int(count) for stem, count in rows}


def _selected(entries: Iterable[FilterEntry]) -> sa.ColumnElement[bool]:
    """Match the documents *entries* select, as :meth:`DocumentFilter._selects` does.

    A document is matched by its description path, the path the filter sees,
    and a file entry also by the stem and ``.assets`` payload it owns.
    """
    description = Document.stem_path + DOCUMENT_EXTENSION
    clauses: list[sa.ColumnElement[bool]] = []

    for entry in entries:
        if entry.directory:
            if not entry.path:
                return sa.true()

            clauses += [
                description == entry.path,
                description.startswith(f"{entry.path}/", autoescape=True),
            ]
            continue

        stem = stem_path_from_reference(entry.path)
        assets_dir = Part(stem, "assets").path
        clauses += [
            description == entry.path,
            Document.stem_path.in_((stem, assets_dir)),
            Document.stem_path.startswith(f"{assets_dir}/", autoescape=True),
        ]

    return sa.or_(sa.false(), *clauses)


def accessible_documents(reach: Reach) -> sa.Select[UUID]:
    """Select the ids of the documents *reach* admits, each store's narrowed by its filter.

    The statement binds a parameter per store and filter entry, never one
    per document.
    """

    def visible(store: Workspace) -> sa.ColumnElement[bool]:
        owned = _workspace_filter(store)

        if (document_filter := reach.filter(store)) is None:
            return owned

        return sa.and_(owned, sa.not_(_selected(document_filter.excluded)))

    return select(Document.id).where(sa.or_(sa.false(), *map(visible, reach.stores)))


def accessible_chunks(reach: Reach) -> sa.ColumnElement[bool]:
    """Match the chunks of the documents *reach* admits.

    Scopes the vector search in one subquery on ``documents``
    (:func:`accessible_documents`), so the scan never ranks a chunk of another
    owner or one the conversation hides.
    """
    return Chunk.document_id.in_(accessible_documents(reach))


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
    converter: str | None

    @classmethod
    def from_entry(cls, entry: EntryMetadata) -> Self:
        """Derive the column values from an entry's metadata."""
        return cls(
            original_suffix=_original_suffix(entry.original_path),
            has_assets=entry.assets_dir is not None,
            entry_kind=EntryKind(entry.entry_kind),
            origin=Origin(entry.origin),
            generated_by=GeneratedBy(entry.generated_by),
            converter=entry.converter,
        )

    def as_values(self) -> dict[str, Any]:
        """Return the column mapping for a SQLAlchemy ``.values()`` / ``set_``."""
        return asdict(self)


async def upsert_document(
    store: Workspace,
    entry: EntryMetadata,
    pipeline: str,
    line_count: int,
) -> DocumentMetadata:
    """Insert or replace the SQL row for a document before indexing chunks.

    Chunk rows are written separately by
    :func:`hivegent.retrieval.index_document` so the embedding +
    INSERT happen inside cbrkit's transaction.  The returned
    :class:`DocumentMetadata` carries an empty ``chunks`` list; the
    orchestrator fills it after calling the indexer.

    The row's ``content_digest``, ``chunker``, and stat columns are cleared here because the
    replacement chunks are not durable yet.  :func:`set_content_state` stamps
    them only after indexing succeeds, so a null digest always means "needs
    re-indexing".  ``line_count`` is written eagerly instead.  It is a pure
    function of the content this row represents, so it is correct regardless of
    whether the chunks land.

    Concurrency-safe ``INSERT ... ON CONFLICT DO UPDATE`` keyed on the
    ``(workspace_key, stem_path)`` unique constraint: two requests racing to
    index the same stem cannot trip the constraint and roll back the
    transaction (losing the document).
    """
    mutable = {
        **_EntryColumns.from_entry(entry).as_values(),
        "pipeline": pipeline,
        "content_digest": None,
        "chunker": None,
        "line_count": line_count,
        **_stat_columns(None),
    }
    workspace_key = _writable_key(store)

    async with session() as s:
        await ensure_workspace(s, store.scope)
        stmt = (
            pg_insert(Document)
            .values(workspace_key=workspace_key, stem_path=entry.stem_path, **mutable)
            .on_conflict_do_update(
                index_elements=[Document.workspace_key, Document.stem_path],
                set_={**mutable, "updated_at": func.now()},
            )
            .returning(Document)
        )
        return _document_from_row((await s.scalars(stmt)).one(), [])


async def update_entry(
    store: Workspace, entry: EntryMetadata, stat: ContentStat | None
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
            .where(_writable_filter(store), Document.stem_path == entry.stem_path)
            .values(**columns, **_stat_columns(stat), updated_at=func.now())
        )
    return affected_rows(result) > 0


async def set_content_state(
    document_id: UUID, content_digest: str, chunker: str, stat: ContentStat | None
) -> None:
    """Stamp a document's content digest, chunker fingerprint, and stat after chunks are durable."""
    async with session() as s:
        await s.execute(
            update(Document)
            .where(Document.id == document_id)
            .values(
                content_digest=content_digest,
                chunker=chunker,
                **_stat_columns(stat),
                updated_at=func.now(),
            )
        )


async def clear_content_states(workspace_key: str | None = None) -> int:
    """Clear the stamps of the documents of a workspace, or of every one, so the next reconcile indexes them again.

    Returns:
        The number of documents.
    """
    stmt = update(Document).values(
        content_digest=None, chunker=None, **_stat_columns(None)
    )

    if workspace_key is not None:
        stmt = stmt.where(Document.workspace_key == workspace_key)

    async with session() as s:
        return affected_rows(await s.execute(stmt))


async def delete_document(
    store: Workspace, reference: str, *, s: AsyncSession | None = None
) -> bool:
    """Delete a document and its chunks.  Returns ``True`` if one was removed."""
    stem_path = stem_path_from_reference(reference)

    async with session(s) as tx:
        result = await tx.execute(
            delete(Document).where(
                _writable_filter(store), Document.stem_path == stem_path
            )
        )

    return affected_rows(result) > 0


async def delete_documents(store: Workspace, references: Sequence[str]) -> int:
    """Delete every document in *references* in one statement.

    Cascades to chunks.  Returns the number of rows removed; batching
    avoids the N+1 of deleting one reference at a time.
    """
    stems = {stem_path_from_reference(ref) for ref in references}
    if not stems:
        return 0
    async with session() as s:
        result = await s.execute(
            delete(Document).where(
                _writable_filter(store), Document.stem_path.in_(stems)
            )
        )
    return affected_rows(result)


async def delete_subtree(
    store: Workspace, prefix: str, *, s: AsyncSession | None = None
) -> int:
    """Delete every document whose stem lies strictly below ``prefix/``.

    A same-named sibling document (stem equal to *prefix*) is left alone.
    Delete it explicitly via :func:`delete_document`.
    """
    if not prefix:
        return 0

    async with session(s) as tx:
        result = await tx.execute(
            delete(Document).where(_writable_filter(store), stem_subtree_filter(prefix))
        )

    return affected_rows(result)


async def move_document(
    src_store: Workspace,
    src_stem: str,
    dst_store: Workspace,
    dst_stem: str,
    *,
    s: AsyncSession | None = None,
) -> bool:
    """Rename a document's ``stem_path``, re-owning it when the store changes.

    Takes raw stem paths, not references: a stem may itself contain dots
    (``a.tar`` from ``a.tar.gz``), so re-deriving it here via
    :func:`stem_path_from_reference` would strip part of the name and
    miss or mis-target the row.  Chunks reference the document by id
    (which never changes) so the vector index needs no reindex after a
    rename.  A cross-store move re-homes the same rows and vectors by
    pointing them at another workspace, again with no re-embed.
    """
    cross_store = src_store != dst_store
    if not cross_store and src_stem == dst_stem:
        return False

    workspace_key = _writable_key(dst_store)

    async with session(s) as tx:
        if cross_store:
            await ensure_workspace(tx, dst_store.scope)

        result = await tx.execute(
            update(Document)
            .where(_writable_filter(src_store), Document.stem_path == src_stem)
            .values(stem_path=dst_stem, workspace_key=workspace_key)
        )

    return affected_rows(result) > 0


async def move_subtree(
    src_store: Workspace,
    src_prefix: str,
    dst_store: Workspace,
    dst_prefix: str,
    *,
    s: AsyncSession | None = None,
) -> None:
    """Move every document strictly below ``src_prefix/`` to ``dst_prefix/``.

    A single bulk UPDATE rewrites the ``src_prefix`` portion of each
    matched ``stem_path`` to ``dst_prefix`` and, for a cross-store move,
    points them at *dst_store*.  No vector reindex needed.
    Chunks reference the immutable document id.  A same-named sibling
    document (stem equal to *src_prefix*) is left alone.  Move it
    explicitly via :func:`move_document`.
    """
    cross_store = src_store != dst_store
    if not src_prefix or (not cross_store and src_prefix == dst_prefix):
        return

    workspace_key = _writable_key(dst_store)

    async with session(s) as tx:
        if cross_store:
            await ensure_workspace(tx, dst_store.scope)

        await tx.execute(
            update(Document)
            .where(_writable_filter(src_store), stem_subtree_filter(src_prefix))
            .values(
                stem_path=func.concat(
                    dst_prefix, func.substr(Document.stem_path, len(src_prefix) + 1)
                ),
                workspace_key=workspace_key,
            )
        )
