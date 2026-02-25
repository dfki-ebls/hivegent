"""Chunk persistence for chunked documents."""

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .chunkers import ChunkingPipeline, get_chunker
from .config import settings
from .retrieval import sync_index
from .store import Casebase
from .types import ChunkedDocument, ChunkInfo

__all__ = [
    "ChunkInfo",
    "ChunkedDocument",
    "chunk_document",
    "delete_chunks",
    "get_chunks",
    "list_chunked_documents",
    "load_chunked_document",
    "rechunk_document",
]

logger = logging.getLogger(__name__)


def _get_chunk_path(store: Casebase, filepath: str) -> Path:
    """Get the path to a chunk JSON file.

    Args:
        store: The casebase.
        filepath: The relative document path.

    Returns:
        Path to the chunk JSON file.
    """
    chunks_dir = store.chunks_dir(settings.data_dir)
    chunk_file_path = chunks_dir / f"{filepath}.json"
    chunk_file_path.parent.mkdir(parents=True, exist_ok=True)
    return chunk_file_path


def chunk_document(
    store: Casebase,
    filename: str,
    content: str,
    chunking_pipeline: ChunkingPipeline = ChunkingPipeline.AUTO,
    chunking_config: dict[str, Any] | None = None,
) -> ChunkedDocument:
    """Chunk a document and persist the results to disk.

    Args:
        store: The casebase.
        filename: The document filename.
        content: The document text content.
        chunking_pipeline: The chunking pipeline to use.
        chunking_config: Optional pipeline-specific configuration.

    Returns:
        The chunked document with metadata.
    """
    chunker = get_chunker(chunking_pipeline, filename=filename)
    raw_chunks = chunker(content, config=chunking_config)

    chunks = [
        ChunkInfo(
            text=c.text,
            token_count=c.token_count,
            start_index=c.start_index,
            end_index=c.end_index,
        )
        for c in raw_chunks
    ]

    doc = ChunkedDocument(
        pipeline=chunker.name,
        chunk_size=chunker.chunk_size,
        created_at=datetime.now(tz=timezone.utc),
        chunks=chunks,
    )

    chunk_path = _get_chunk_path(store, filename)
    chunk_path.write_text(doc.model_dump_json(indent=2), encoding="utf-8")

    return doc


def load_chunked_document(chunks_dir: Path, filename: str) -> ChunkedDocument | None:
    """Load a chunked document from a directory by original filename.

    Args:
        chunks_dir: Directory containing chunk JSON files.
        filename: The original document filename.

    Returns:
        The chunked document, or None if not found.
    """
    chunk_path = chunks_dir / f"{filename}.json"
    if not chunk_path.exists():
        return None

    try:
        data = json.loads(chunk_path.read_text(encoding="utf-8"))
        return ChunkedDocument.model_validate(data)
    except (json.JSONDecodeError, Exception) as e:
        logger.warning("Failed to load chunks for %s: %s", filename, e)
        return None


def get_chunks(store: Casebase, filename: str) -> ChunkedDocument | None:
    """Load chunks for a document from disk.

    Args:
        store: The casebase.
        filename: The document filename.

    Returns:
        The chunked document, or None if not found.
    """
    return load_chunked_document(store.chunks_dir(settings.data_dir), filename)


def delete_chunks(store: Casebase, filepath: str) -> bool:
    """Delete chunk file for a document.

    After unlinking, cleans up empty parent directories up to the chunks root.

    Args:
        store: The casebase.
        filepath: The relative document path.

    Returns:
        True if the chunk file was deleted, False if it didn't exist.
    """
    chunks_dir = store.chunks_dir(settings.data_dir)
    chunk_path = chunks_dir / f"{filepath}.json"
    if chunk_path.exists():
        chunk_path.unlink()
        # Clean up empty parent directories up to chunks_dir
        parent = chunk_path.parent
        while parent != chunks_dir:
            try:
                parent.rmdir()
            except OSError:
                break
            parent = parent.parent

        return True
    return False


def list_chunked_documents(store: Casebase) -> dict[str, int]:
    """List all chunked documents for a store with their chunk counts.

    Args:
        store: The casebase.

    Returns:
        Dict mapping document filename to chunk count.
    """
    chunks_dir = store.chunks_dir(settings.data_dir)
    if not chunks_dir.exists():
        return {}

    result: dict[str, int] = {}
    for path in chunks_dir.rglob("*.json"):
        doc_filepath = str(path.relative_to(chunks_dir).as_posix()).removesuffix(
            ".json"
        )
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            result[doc_filepath] = len(data.get("chunks", []))
        except (json.JSONDecodeError, Exception):
            continue

    return result


def rechunk_document(store: Casebase, filename: str) -> None:
    """Re-chunk a document and sync the search index.

    Reads the file from the store's documents directory, re-chunks it,
    and rebuilds the LanceDB index.

    Args:
        store: The casebase.
        filename: The relative document path.
    """
    docs_dir = store.documents_dir(settings.data_dir)
    file_path = docs_dir / filename
    try:
        text_content = file_path.read_text(encoding="utf-8")
        chunk_document(store, filename, text_content)
        sync_index(store)
    except Exception:
        logger.warning("Re-chunking failed for %s after write", filename)
