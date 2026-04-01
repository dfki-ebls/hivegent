"""Chunk persistence for chunked documents."""

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated, override

from pydantic import Field

from .chunkers import ChunkingPipeline, ChunkingSpec, get_chunker
from .chunkers.base import (
    ChunkData,
    ChunkSummary,
    DocumentMetadata,
    EntryMetadata,
)
from .config import settings
from .entries import (
    metadata_path_for_reference,
    resolve_entry_paths,
    stem_path_from_reference,
)
from .store import Casebase
from .tools.base import PathsTool, ToolOutput, file_allowed, resolve_search_path

_NOT_FOUND_MSG = "(document not found)"

__all__ = [
    "ChunkIndexArg",
    "ChunkData",
    "ChunkSummary",
    "DocumentMetadata",
    "GetChunkTool",
    "ListChunksTool",
    "chunk_document",
    "delete_metadata",
    "get_metadata",
    "list_chunked_documents",
    "load_document_metadata",
    "on_document_write",
    "rechunk_document",
]

logger = logging.getLogger(__name__)

ChunkIndexArg = Annotated[
    int,
    Field(description="Zero-based index of the chunk to retrieve.", ge=0),
]


@dataclass(slots=True, frozen=True)
class ListChunksTool(PathsTool[list[ChunkSummary] | None]):
    """List chunk metadata for a document."""

    @override
    def __call__(self, filename: str) -> ToolOutput[list[ChunkSummary] | None]:
        """List chunk metadata for a document."""
        resolved = resolve_search_path(self.resolved_paths, filename)
        if resolved is None:
            return ToolOutput(data=None, formatted=_NOT_FOUND_MSG)
        sp, local = resolved
        if not file_allowed(sp.filter_func, local):
            return ToolOutput(data=None, formatted=_NOT_FOUND_MSG)
        metadata = load_document_metadata(sp.path, local)
        if not metadata:
            return ToolOutput(data=None, formatted=_NOT_FOUND_MSG)
        result = [
            ChunkSummary(
                token_count=chunk.token_count,
                start_index=chunk.start_index,
                end_index=chunk.end_index,
            )
            for chunk in metadata.chunks
        ]
        if not result:
            return ToolOutput(data=result, formatted="(no chunks)")
        lines: list[str] = []
        for i, c in enumerate(result):
            lines.append(
                f"#{i}  chars {c.start_index}-{c.end_index}"
                f"  ({c.token_count} tokens)"
            )
        return ToolOutput(data=result, formatted="\n".join(lines))


@dataclass(slots=True, frozen=True)
class GetChunkTool(PathsTool[str | None]):
    """Get the content of a specific chunk."""

    @override
    def __call__(
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
        metadata = load_document_metadata(sp.path, local)
        if not metadata:
            return ToolOutput(data=None)
        if 0 <= chunk_index < len(metadata.chunks):
            return ToolOutput(data=metadata.chunks[chunk_index].text)
        return ToolOutput(data=None)


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


async def chunk_document(
    store: Casebase,
    filename: str,
    content: str,
    chunking: ChunkingSpec | None = None,
    *,
    entry_metadata: EntryMetadata | None = None,
) -> DocumentMetadata:
    """Chunk a document and persist the results to disk.

    Args:
        store: The casebase.
        filename: The document filename.
        content: The document text content.
        chunking: The chunking spec (pipeline + config).
        entry_metadata: Optional logical-entry metadata.

    Returns:
        The document metadata with chunks.
    """
    spec = chunking or ChunkingSpec()

    if entry_metadata is not None:
        resolved_entry_metadata = entry_metadata
    else:
        existing = get_metadata(store, filename)
        if existing is not None:
            # DocumentMetadata is a subclass of EntryMetadata; extract base fields.
            resolved_entry_metadata = EntryMetadata.model_validate(
                existing.model_dump(include=set(EntryMetadata.model_fields))
            )
        else:
            workspace_dir = store.workspace_dir(settings.data_dir)
            resolved = resolve_entry_paths(workspace_dir, filename)
            resolved_entry_metadata = _default_entry_metadata(
                resolved.description_path,
                resolved.original_path,
                resolved.assets_dir,
            )

    # Generated descriptions are always stored as a single chunk.
    if resolved_entry_metadata.generated_by in ("vision", "stub"):
        spec = ChunkingSpec(pipeline=ChunkingPipeline.NONE)
    chunker = get_chunker(
        spec.pipeline,
        content_length=len(content),
        config=spec.config,
    )
    raw_chunks = await chunker(content)

    doc = DocumentMetadata(
        **resolved_entry_metadata.model_dump(),
        pipeline=chunker.name,
        created_at=datetime.now(tz=timezone.utc),
        chunks=raw_chunks,
    )

    meta_path = metadata_path_for_reference(store, filename)
    meta_path.write_text(
        doc.model_dump_json(indent=2, exclude_none=True),
        encoding="utf-8",
    )

    return doc


def load_document_metadata(
    metadata_dir: Path,
    filename: str,
) -> DocumentMetadata | None:
    """Load document metadata from a directory by document filename.

    Args:
        metadata_dir: Directory containing metadata JSON files.
        filename: The document filename (e.g. ``"report.md"``).

    Returns:
        The document metadata, or ``None`` if not found.
    """
    stem = stem_path_from_reference(filename)
    meta_path = metadata_dir / f"{stem}.json"
    try:
        return DocumentMetadata.model_validate_json(
            meta_path.read_text(encoding="utf-8")
        )
    except FileNotFoundError:
        return None
    except Exception as e:
        logger.warning("Failed to load metadata for %s: %s", filename, e)
        return None


def get_metadata(store: Casebase, filename: str) -> DocumentMetadata | None:
    """Load metadata for a document from disk.

    Args:
        store: The casebase.
        filename: The document filename.

    Returns:
        The document metadata, or ``None`` if not found.
    """
    return load_document_metadata(store.metadata_dir(settings.data_dir), filename)


def delete_metadata(store: Casebase, filepath: str) -> bool:
    """Delete metadata file for a document.

    After unlinking, cleans up empty parent directories up to the
    metadata root.

    Args:
        store: The casebase.
        filepath: The relative document path.

    Returns:
        True if the metadata file was deleted, False if it didn't exist.
    """
    metadata_dir = store.metadata_dir(settings.data_dir)
    meta_path = metadata_path_for_reference(store, filepath)
    try:
        meta_path.unlink()
    except FileNotFoundError:
        return False
    parent = meta_path.parent
    while parent != metadata_dir:
        try:
            parent.rmdir()
        except OSError:
            break
        parent = parent.parent

    return True


def list_chunked_documents(store: Casebase) -> dict[str, int]:
    """List all chunked documents for a store with their chunk counts.

    Reconstructs the workspace filename by appending ``DOCUMENT_EXTENSION``
    since metadata files use the stem-only naming convention.

    Args:
        store: The casebase.

    Returns:
        Dict mapping document filename to chunk count.
    """
    metadata_dir = store.metadata_dir(settings.data_dir)
    if not metadata_dir.exists():
        return {}

    result: dict[str, int] = {}
    for path in metadata_dir.rglob("*.json"):
        try:
            document = DocumentMetadata.model_validate_json(
                path.read_text(encoding="utf-8")
            )
            result[document.description_path] = len(document.chunks)
        except Exception:
            continue

    return result


async def rechunk_document(
    store: Casebase,
    filename: str,
    chunking: ChunkingSpec | None = None,
) -> None:
    """Re-chunk a document and persist metadata.

    Reads the file from the store's documents directory, re-chunks it,
    and writes the metadata JSON while preserving the logical entry fields.
    Does **not** sync the search index; the caller should mark the store
    dirty via :func:`~hivegent.retrieval.mark_dirty`.

    Args:
        store: The casebase.
        filename: The relative document path.
        chunking: Optional chunking spec (pipeline + config).
    """
    workspace = store.workspace_dir(settings.data_dir)
    file_path = workspace / filename
    try:
        text_content = file_path.read_text(encoding="utf-8")
        await chunk_document(store, filename, text_content, chunking)
    except Exception:
        logger.warning("Re-chunking failed for %s after write", filename)


async def on_document_write(store: Casebase, filename: str) -> None:
    """Re-chunk a document and mark the search index dirty.

    Intended as the ``on_write`` callback for mutation tools.

    Args:
        store: The casebase the document belongs to.
        filename: The relative document path that was written.
    """
    from .retrieval import mark_dirty_and_sync

    await rechunk_document(store, filename)
    mark_dirty_and_sync(store)
