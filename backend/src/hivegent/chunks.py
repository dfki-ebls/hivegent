"""Document chunking and persistence coordinator.

All operations flow through :mod:`hivegent.db.documents` (source of
truth) and :mod:`hivegent.retrieval` (derived index).

The two agent tools — :class:`ListChunksTool` and :class:`GetChunkTool`
— live here for now until ``tools/chunks.py`` lands.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Any, cast, override

from pydantic import Field

from .chunkers import ChunkingPipeline, ChunkingSpec, get_chunker
from .chunkers.base import (
    ChunkData,
    ChunkSummary,
    DocumentMetadata,
    EntryMetadata,
)
from .config import settings
from .db import documents as db_documents
from .entries import (
    description_path_for_stem,
    resolve_entry_paths,
    stem_path_from_reference,
)
from .retrieval import index_document, unindex_paths
from .store import Casebase
from .tools.base import AsyncPathTool, ToolOutput, file_allowed, resolve_search_path

_NOT_FOUND_MSG = "(document not found)"

__all__ = [
    "ChunkData",
    "ChunkIndexArg",
    "ChunkSummary",
    "DocumentMetadata",
    "GetChunkTool",
    "ListChunksTool",
    "chunk_and_index_document",
    "delete_document",
]

logger = logging.getLogger(__name__)

ChunkIndexArg = Annotated[
    int,
    Field(description="Zero-based index of the chunk to retrieve.", ge=0),
]


# ─── Agent tools ───────────────────────────────────────────────────────


def _path_to_casebase(workspace_path: Path) -> Casebase | None:
    """Reverse-map ``data/workspace/<kind>/<id>/`` back to a Casebase."""
    try:
        rel = workspace_path.resolve().relative_to(
            (settings.data_dir / "workspace").resolve()
        )
    except ValueError:
        return None
    parts = rel.parts
    if len(parts) < 2 or parts[0] not in ("user", "group"):
        return None
    try:
        return Casebase(kind=cast(Any, parts[0]), id=parts[1])
    except ValueError:
        return None


@dataclass(slots=True, frozen=True)
class ListChunksTool(AsyncPathTool[list[ChunkSummary] | None]):
    """List chunk metadata for a document."""

    @override
    async def __call__(self, filename: str) -> ToolOutput[list[ChunkSummary] | None]:
        """List chunk metadata for a document."""
        resolved = resolve_search_path(self.resolved_paths, filename)
        if resolved is None:
            return ToolOutput(data=None, formatted=_NOT_FOUND_MSG)
        sp, local = resolved
        if not file_allowed(sp.filter_func, local):
            return ToolOutput(data=None, formatted=_NOT_FOUND_MSG)
        store = _path_to_casebase(sp.path)
        if store is None:
            return ToolOutput(data=None, formatted=_NOT_FOUND_MSG)
        doc = await db_documents.get_document(store, local)
        if doc is None:
            return ToolOutput(data=None, formatted=_NOT_FOUND_MSG)
        result = [
            ChunkSummary(
                token_count=c.token_count,
                start_index=c.start_index,
                end_index=c.end_index,
                start_line=c.start_line,
                end_line=c.end_line,
            )
            for c in doc.chunks
        ]
        if not result:
            return ToolOutput(data=result, formatted="(no chunks)")
        lines = [
            f"#{i}  lines {c.start_line}-{c.end_line}"
            f"  chars {c.start_index}-{c.end_index}"
            f"  ({c.token_count} tokens)"
            for i, c in enumerate(result)
        ]
        return ToolOutput(data=result, formatted="\n".join(lines))


@dataclass(slots=True, frozen=True)
class GetChunkTool(AsyncPathTool[str | None]):
    """Get the content of a specific chunk."""

    @override
    async def __call__(
        self,
        filename: str,
        chunk_index: ChunkIndexArg,
    ) -> ToolOutput[str | None]:
        """Get the content of a specific chunk."""
        resolved = resolve_search_path(self.resolved_paths, filename)
        if resolved is None:
            return ToolOutput(data=None)
        sp, local = resolved
        if not file_allowed(sp.filter_func, local):
            return ToolOutput(data=None)
        store = _path_to_casebase(sp.path)
        if store is None:
            return ToolOutput(data=None)
        doc = await db_documents.get_document(store, local)
        if doc is None:
            return ToolOutput(data=None)
        if 0 <= chunk_index < len(doc.chunks):
            return ToolOutput(data=doc.chunks[chunk_index].text)
        return ToolOutput(data=None)


# ─── Writer / coordinator ─────────────────────────────────────────────


def _default_entry_metadata(
    filename: str,
    resolved_original_path: str | None,
    resolved_assets_dir: str | None,
) -> EntryMetadata:
    """Build the default logical-entry metadata for a markdown file."""
    import mimetypes

    files = [filename]
    if resolved_original_path is not None:
        files.append(resolved_original_path)
    return EntryMetadata(
        entry_kind="user_markdown",
        stem_path=stem_path_from_reference(filename),
        description_path=filename,
        original_path=resolved_original_path,
        assets_dir=resolved_assets_dir,
        mime=mimetypes.guess_type(resolved_original_path or filename)[0],
        origin="upload",
        generated_by="user",
        files=files,
    )


async def chunk_and_index_document(
    store: Casebase,
    filename: str,
    content: str,
    chunking: ChunkingSpec | None = None,
    *,
    entry_metadata: EntryMetadata | None = None,
) -> DocumentMetadata:
    """Chunk a document, persist to SQL, and upsert into LanceDB.

    Args:
        store: The casebase.
        filename: The document filename (workspace-relative markdown).
        content: The document text content.
        chunking: The chunking spec (pipeline + config).
        entry_metadata: Optional logical-entry metadata.  Defaults are
            derived from the workspace layout when omitted.

    Returns:
        The persisted document metadata (chunks + entry header).
    """
    spec = chunking or ChunkingSpec()

    if entry_metadata is None:
        existing = await db_documents.get_document(store, filename)
        if existing is not None:
            entry_metadata = EntryMetadata.model_validate(
                existing.model_dump(include=set(EntryMetadata.model_fields))
            )
        else:
            workspace_dir = store.workspace_dir(settings.data_dir)
            resolved = resolve_entry_paths(workspace_dir, filename)
            entry_metadata = _default_entry_metadata(
                resolved.description_path,
                resolved.original_path,
                resolved.assets_dir,
            )

    if entry_metadata.generated_by in ("vision", "stub"):
        spec = ChunkingSpec(pipeline=ChunkingPipeline.NONE)
    chunker = get_chunker(
        spec.pipeline,
        content_length=len(content),
        config=spec.config,
    )
    raw_chunks = await chunker(content, mime=entry_metadata.mime)

    doc = await db_documents.upsert_document(
        store, entry_metadata, pipeline=chunker.name, chunks=raw_chunks
    )
    await index_document(store, filename, doc)
    return doc


async def delete_document(store: Casebase, filepath: str) -> bool:
    """Remove a document and its chunks from SQL and LanceDB.

    Returns:
        ``True`` if the SQL row existed and was deleted.
    """
    removed = await db_documents.delete_document(store, filepath)
    description_path = description_path_for_stem(stem_path_from_reference(filepath))
    try:
        await unindex_paths(store, [description_path])
    except Exception:
        logger.warning(
            "Failed to unindex %s/%s", store.store_key, description_path, exc_info=True
        )
    return removed
