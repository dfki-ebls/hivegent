"""Chunk persistence for chunked documents."""

import json
import logging
from collections.abc import Sequence
from datetime import datetime, timezone
from pathlib import Path

from .chunkers import ChunkingSpec, get_chunker
from .config import DOCUMENT_EXTENSION, settings
from .store import Casebase
from .types import ChunkInfo, DocumentMetadata

__all__ = [
    "ChunkInfo",
    "DocumentMetadata",
    "chunk_document",
    "delete_metadata",
    "get_metadata",
    "list_chunked_documents",
    "load_document_metadata",
    "rechunk_document",
]

logger = logging.getLogger(__name__)


def _get_metadata_path(store: Casebase, filepath: str) -> Path:
    """Get the path to a metadata JSON file.

    Strips the ``.md`` document extension before forming the path so that
    ``report.md`` is stored as ``metadata/report.json`` rather than
    ``metadata/report.md.json``.

    Args:
        store: The casebase.
        filepath: The relative document path (e.g. ``"report.md"``).

    Returns:
        Path to the metadata JSON file.
    """
    metadata_dir = store.metadata_dir(settings.data_dir)
    stem = filepath.removesuffix(DOCUMENT_EXTENSION)
    meta_path = metadata_dir / f"{stem}.json"
    meta_path.parent.mkdir(parents=True, exist_ok=True)
    return meta_path


async def chunk_document(
    store: Casebase,
    filename: str,
    content: str,
    chunking: ChunkingSpec | None = None,
    *,
    images: Sequence[str] | None = None,
) -> DocumentMetadata:
    """Chunk a document and persist the results to disk.

    Args:
        store: The casebase.
        filename: The document filename.
        content: The document text content.
        chunking: The chunking spec (pipeline + config).
        images: Optional workspace-relative paths to companion images.

    Returns:
        The document metadata with chunks.
    """
    spec = chunking or ChunkingSpec()
    chunker = get_chunker(
        spec.pipeline, content_length=len(content), config=spec.config
    )
    raw_chunks = await chunker(content)

    chunks = [
        ChunkInfo(
            text=c.text,
            token_count=c.token_count,
            start_index=c.start_index,
            end_index=c.end_index,
        )
        for c in raw_chunks
    ]

    doc = DocumentMetadata(
        pipeline=chunker.name,
        created_at=datetime.now(tz=timezone.utc),
        chunks=chunks,
        images=list(images) if images else [],
    )

    meta_path = _get_metadata_path(store, filename)
    meta_path.write_text(doc.model_dump_json(indent=2), encoding="utf-8")

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
    stem = filename.removesuffix(DOCUMENT_EXTENSION)
    meta_path = metadata_dir / f"{stem}.json"
    try:
        data = json.loads(meta_path.read_text(encoding="utf-8"))
        return DocumentMetadata.model_validate(data)
    except FileNotFoundError:
        return None
    except (json.JSONDecodeError, Exception) as e:
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
    stem = filepath.removesuffix(DOCUMENT_EXTENSION)
    meta_path = metadata_dir / f"{stem}.json"
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
        stem = str(path.relative_to(metadata_dir).as_posix()).removesuffix(".json")
        doc_filepath = stem + DOCUMENT_EXTENSION
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            result[doc_filepath] = len(data.get("chunks", []))
        except (json.JSONDecodeError, Exception):
            continue

    return result


async def rechunk_document(
    store: Casebase,
    filename: str,
    chunking: ChunkingSpec | None = None,
) -> None:
    """Re-chunk a document and persist metadata.

    Reads the file from the store's documents directory, re-chunks it,
    and writes the metadata JSON.  Preserves the existing images list.
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
        # Preserve existing image references.
        existing = get_metadata(store, filename)
        existing_images = existing.images if existing else []
        await chunk_document(
            store,
            filename,
            text_content,
            chunking,
            images=existing_images,
        )
    except Exception:
        logger.warning("Re-chunking failed for %s after write", filename)
