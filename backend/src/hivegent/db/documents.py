"""Document + chunk repository.

Replaces ``hivegent.chunks``' on-disk metadata JSONs with SQL rows.
The Pydantic ``DocumentMetadata`` shape stays the schema of record at
the API boundary; this module is the only translator between rows and
that shape.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path, PurePosixPath

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ._common import affected_rows, ensure_group, ensure_user, stem_subtree_filter

from ..chunkers.base import (
    ChunkData,
    DocumentMetadata,
    EntryMetadata,
)
from ..config import settings
from ..entries import (
    assets_dir_for_stem,
    description_path_for_stem,
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
    "delete_document",
    "delete_subtree",
    "get_document",
    "list_document_paths",
    "move_document",
    "move_subtree",
    "upsert_document",
]


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
    """Build an :class:`EntryMetadata` from a row, deriving the path columns.

    ``description_path``, ``original_path``, ``assets_dir``, and the
    ``files`` array are derived from ``stem_path`` + ``original_ext`` +
    ``has_assets``.  ``files`` for an entry with assets includes a
    recursive walk of its assets directory when *workspace_root* is
    provided.
    """
    description_path = description_path_for_stem(doc.stem_path)
    original_path = f"{doc.stem_path}.{doc.original_ext}" if doc.original_ext else None
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
    doc: Document, workspace_root: Path | None = None
) -> DocumentMetadata:
    entry = _entry_from_row(doc, workspace_root)
    return DocumentMetadata(
        **entry.model_dump(),
        pipeline=doc.pipeline,
        created_at=doc.created_at,
        chunks=[
            ChunkData(
                text=c.text,
                token_count=c.token_count,
                start_index=c.start_index,
                end_index=c.end_index,
                start_line=c.start_line,
                end_line=c.end_line,
                index=c.idx,
            )
            for c in doc.chunks
        ],
    )


# ─── Helpers ──────────────────────────────────────────────────────────


def _original_ext(original_path: str | None) -> str | None:
    """Return the extension (without leading dot) of *original_path*."""
    if not original_path:
        return None
    return PurePosixPath(original_path).suffix.lstrip(".") or None


async def _ensure_owner(s: AsyncSession, store: Casebase) -> None:
    """Lazily materialize the owning user/group row before an insert."""
    if store.kind == "user":
        await ensure_user(s, store.id)
    else:
        await ensure_group(s, store.id)


async def _find(
    s: AsyncSession,
    store: Casebase,
    stem_path: str,
    *,
    with_chunks: bool = False,
) -> Document | None:
    stmt = select(Document).where(_owner_filter(store), Document.stem_path == stem_path)
    if with_chunks:
        stmt = stmt.options(selectinload(Document.chunks))
    return (await s.execute(stmt)).scalar_one_or_none()


# ─── Reads ─────────────────────────────────────────────────────────────


async def get_document(store: Casebase, reference: str) -> DocumentMetadata | None:
    """Load a document and its chunks by workspace-relative reference."""
    stem_path = stem_path_from_reference(reference)
    async with session() as s:
        row = await _find(s, store, stem_path, with_chunks=True)
        if row is None:
            return None
        workspace_root = store.workspace_path(settings.data_dir)
        return _document_from_row(row, workspace_root)


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


# ─── Writes ────────────────────────────────────────────────────────────


async def upsert_document(
    store: Casebase,
    entry: EntryMetadata,
    pipeline: str,
    chunks: Sequence[ChunkData],
    *,
    content_sha256: str | None = None,
) -> DocumentMetadata:
    """Insert or replace a document and its chunks in one transaction.

    The returned ``DocumentMetadata`` reflects the persisted state.
    """
    async with session() as s:
        await _ensure_owner(s, store)
        doc = await _find(s, store, entry.stem_path)
        if doc is None:
            doc = Document(
                **_owner_kwargs(store),
                stem_path=entry.stem_path,
                original_ext=_original_ext(entry.original_path),
                has_assets=entry.assets_dir is not None,
                entry_kind=EntryKind(entry.entry_kind),
                origin=Origin(entry.origin),
                generated_by=GeneratedBy(entry.generated_by),
                mime=entry.mime,
                pipeline=pipeline,
                content_sha256=content_sha256,
            )
            s.add(doc)
            await s.flush()
        else:
            doc.original_ext = _original_ext(entry.original_path)
            doc.has_assets = entry.assets_dir is not None
            doc.entry_kind = EntryKind(entry.entry_kind)
            doc.origin = Origin(entry.origin)
            doc.generated_by = GeneratedBy(entry.generated_by)
            doc.mime = entry.mime
            doc.pipeline = pipeline
            doc.content_sha256 = content_sha256
            await s.execute(delete(Chunk).where(Chunk.document_id == doc.id))

        for i, c in enumerate(chunks):
            s.add(
                Chunk(
                    document_id=doc.id,
                    idx=i,
                    text=c.text,
                    token_count=c.token_count,
                    start_index=c.start_index,
                    end_index=c.end_index,
                    start_line=c.start_line,
                    end_line=c.end_line,
                )
            )
        doc_id = doc.id

    # Reload for the boundary type (fresh session, eager-loaded chunks).
    async with session() as s:
        row = await s.get(Document, doc_id, options=[selectinload(Document.chunks)])
        assert row is not None
        workspace_root = store.workspace_path(settings.data_dir)
        return _document_from_row(row, workspace_root)


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


async def move_document(
    store: Casebase, src_reference: str, dst_reference: str
) -> bool:
    """Rename a document's ``stem_path``.  No chunk-row work needed (FK is by id)."""
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


async def move_subtree(
    store: Casebase, src_prefix: str, dst_prefix: str
) -> list[tuple[str, str]]:
    """Rename every document under ``src_prefix`` to live under ``dst_prefix``.

    Returns a list of ``(old_stem, new_stem)`` pairs so the caller can
    reindex LanceDB for each renamed document.
    """
    if not src_prefix or src_prefix == dst_prefix:
        return []
    async with session() as s:
        rows = (
            await s.execute(
                select(Document.id, Document.stem_path).where(
                    _owner_filter(store), stem_subtree_filter(src_prefix)
                )
            )
        ).all()
        moves: list[tuple[str, str]] = []
        for doc_id, old_stem in rows:
            if old_stem == src_prefix:
                new_stem = dst_prefix
            else:
                new_stem = dst_prefix + old_stem[len(src_prefix) :]
            await s.execute(
                update(Document).where(Document.id == doc_id).values(stem_path=new_stem)
            )
            moves.append((old_stem, new_stem))
    return moves
