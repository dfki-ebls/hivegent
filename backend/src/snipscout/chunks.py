"""Chunk persistence and search for chunked documents."""

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

import bm25s
from pydantic import BaseModel, Field

from .chunkers import ChunkingPipeline, get_chunker
from .config import settings

__all__ = [
    "ChunkInfo",
    "ChunkedDocument",
    "chunk_document",
    "delete_chunks",
    "get_chunks",
    "list_chunked_documents",
    "load_chunked_document",
    "search_chunks",
]

logger = logging.getLogger(__name__)


class ChunkInfo(BaseModel):
    """A single chunk within a chunked document."""

    text: str = Field(description="The chunk text content")
    token_count: int = Field(description="Number of tokens in the chunk")
    start_index: int = Field(description="Start character index in original document")
    end_index: int = Field(description="End character index in original document")
    index: int = Field(description="Chunk index within the document")


class ChunkedDocument(BaseModel):
    """A document that has been chunked, with metadata."""

    chunking_pipeline: str = Field(description="The chunking pipeline used")
    chunk_size: int = Field(description="The target chunk size in tokens")
    created_at: datetime = Field(description="When the chunks were created")
    chunk_count: int = Field(description="Total number of chunks")
    chunks: list[ChunkInfo] = Field(description="The document chunks")


def _get_chunk_path(user_id: str, filename: str) -> Path:
    """Get the path to a chunk JSON file.

    Args:
        user_id: The user ID.
        filename: The document filename.

    Returns:
        Path to the chunk JSON file.
    """
    chunks_dir = settings.get_user_chunks_dir(user_id)
    return chunks_dir / f"{filename}.json"


def chunk_document(
    user_id: str,
    filename: str,
    content: str,
    chunking_pipeline: ChunkingPipeline = ChunkingPipeline.AUTO,
    chunk_size: int = 2048,
) -> ChunkedDocument:
    """Chunk a document and persist the results to disk.

    Args:
        user_id: The user ID.
        filename: The document filename.
        content: The document text content.
        chunking_pipeline: The chunking pipeline to use.
        chunk_size: The target chunk size in tokens.

    Returns:
        The chunked document with metadata.
    """
    chunker = get_chunker(chunking_pipeline, filename=filename, chunk_size=chunk_size)
    raw_chunks = chunker.chunk(content)

    resolved_pipeline = chunking_pipeline.value
    if chunking_pipeline == ChunkingPipeline.AUTO:
        resolved_pipeline = f"auto ({chunker.name})"

    chunks = [
        ChunkInfo(
            text=c.text,
            token_count=c.token_count,
            start_index=c.start_index,
            end_index=c.end_index,
            index=c.index,
        )
        for c in raw_chunks
    ]

    doc = ChunkedDocument(
        chunking_pipeline=resolved_pipeline,
        chunk_size=chunk_size,
        created_at=datetime.now(tz=timezone.utc),
        chunk_count=len(chunks),
        chunks=chunks,
    )

    chunk_path = _get_chunk_path(user_id, filename)
    chunk_path.write_text(doc.model_dump_json(indent=2), encoding="utf-8")

    return doc


def load_chunked_document(
    chunks_dir: Path, filename: str
) -> ChunkedDocument | None:
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


def get_chunks(user_id: str, filename: str) -> ChunkedDocument | None:
    """Load chunks for a document from disk.

    Args:
        user_id: The user ID.
        filename: The document filename.

    Returns:
        The chunked document, or None if not found.
    """
    return load_chunked_document(settings.get_user_chunks_dir(user_id), filename)


def delete_chunks(user_id: str, filename: str) -> bool:
    """Delete chunk file for a document.

    Args:
        user_id: The user ID.
        filename: The document filename.

    Returns:
        True if the chunk file was deleted, False if it didn't exist.
    """
    chunk_path = _get_chunk_path(user_id, filename)
    if chunk_path.exists():
        chunk_path.unlink()
        return True
    return False


def list_chunked_documents(user_id: str) -> dict[str, int]:
    """List all chunked documents for a user with their chunk counts.

    Args:
        user_id: The user ID.

    Returns:
        Dict mapping document filename to chunk count.
    """
    chunks_dir = settings.get_user_chunks_dir(user_id)
    if not chunks_dir.exists():
        return {}

    result: dict[str, int] = {}
    for path in chunks_dir.glob("*.json"):
        # Filename is stored as "{original_filename}.json"
        doc_filename = path.name.removesuffix(".json")
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            result[doc_filename] = data.get("chunk_count", 0)
        except (json.JSONDecodeError, Exception):
            continue

    return result


def search_chunks(
    user_id: str,
    query: str,
    top_k: int = 5,
) -> list[dict]:
    """Search across all user chunks using BM25.

    Loads chunks from disk, builds a temporary BM25 index, and returns
    the most relevant chunks.

    Args:
        user_id: The user ID.
        query: The search query.
        top_k: Maximum number of results to return.

    Returns:
        List of dicts with filename, chunk_index, text, token_count, score.
    """
    chunks_dir = settings.get_user_chunks_dir(user_id)
    if not chunks_dir.exists():
        return []

    # Collect all chunks from all documents
    all_chunks: list[dict] = []
    all_texts: list[str] = []

    for path in sorted(chunks_dir.glob("*.json")):
        doc_filename = path.name.removesuffix(".json")
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            doc = ChunkedDocument.model_validate(data)
        except (json.JSONDecodeError, Exception):
            continue

        for chunk in doc.chunks:
            all_chunks.append({
                "filename": doc_filename,
                "chunk_index": chunk.index,
                "text": chunk.text,
                "token_count": chunk.token_count,
            })
            all_texts.append(chunk.text)

    if not all_chunks:
        return []

    # Build temporary BM25 index
    corpus_tokens = bm25s.tokenize(all_texts)
    retriever = bm25s.BM25()
    retriever.index(corpus_tokens)

    query_tokens = bm25s.tokenize([query])
    indices, scores = retriever.retrieve(query_tokens, k=min(top_k, len(all_chunks)))

    results: list[dict] = []
    for idx, score in zip(indices[0], scores[0]):
        if idx < len(all_chunks):
            entry = all_chunks[idx].copy()
            entry["score"] = round(float(score), 4)
            results.append(entry)

    return results
