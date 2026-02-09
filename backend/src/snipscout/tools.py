"""Shared path-scoped tool generators used by agent and MCP wrappers."""

import json
from dataclasses import dataclass
from fnmatch import fnmatch
from pathlib import Path

import bm25s
from ripgrepy import Ripgrepy

from .chunks import ChunkedDocument
from .config import TEXT_EXTENSIONS
from .documents import create_index
from .documents import search_documents as bm25_search_documents
from .types import (
    ChunkSummary,
    DocumentRange,
    DocumentSummary,
    GrepMatch,
    RetrievedChunk,
    RetrievedDocument,
)

__all__ = [
    "GetChunkTool",
    "GetDocumentLinesTool",
    "GetDocumentTool",
    "GlobDocumentsTool",
    "GrepTool",
    "ListChunksTool",
    "ListDocumentsTool",
    "SearchChunksTool",
    "SearchDocumentsTool",
]


def _load_documents(path: Path) -> dict[str, str]:
    """Load supported text documents from a directory."""
    documents: dict[str, str] = {}
    if not path.exists():
        return documents

    for ext in TEXT_EXTENSIONS:
        for file_path in sorted(path.glob(f"*{ext}")):
            if file_path.is_file():
                documents[file_path.name] = file_path.read_text(encoding="utf-8")
    return documents


def _load_chunked_document(path: Path, filename: str) -> ChunkedDocument | None:
    """Load a chunk JSON document by original filename."""
    chunk_path = path / f"{filename}.json"
    if not chunk_path.exists():
        return None
    try:
        data = json.loads(chunk_path.read_text(encoding="utf-8"))
        return ChunkedDocument.model_validate(data)
    except Exception:
        return None


@dataclass(slots=True, frozen=True)
class ListDocumentsTool:
    """List all available documents with their sizes in bytes."""

    path: Path

    def __call__(self) -> list[DocumentSummary]:
        """List all available documents with their sizes in bytes."""
        if not self.path.exists():
            return []
        return [
            DocumentSummary(filename=f.name, size=f.stat().st_size)
            for f in self.path.iterdir()
            if f.is_file()
        ]


@dataclass(slots=True, frozen=True)
class GetDocumentTool:
    """Get the full content of a specific document."""

    path: Path

    def __call__(self, filename: str) -> str | None:
        """Get the full content of a specific document.

        Args:
            filename: The exact filename to retrieve.
        """
        documents = _load_documents(self.path)
        return documents.get(filename)


@dataclass(slots=True, frozen=True)
class GetDocumentLinesTool:
    """Get a range of lines from a document."""

    path: Path

    def __call__(
        self,
        filename: str,
        start: int = 1,
        end: int | None = None,
    ) -> DocumentRange | None:
        """Get a range of lines from a document.

        Args:
            filename: The document filename.
            start: First line to include (1-indexed, default: 1).
            end: Last line to include (1-indexed, default: end of file).
        """
        documents = _load_documents(self.path)
        if filename not in documents:
            return None

        lines = documents[filename].splitlines()
        total = len(lines)
        start = max(1, start)
        end = min(total, end) if end else total

        return DocumentRange(
            start_line=start,
            end_line=end,
            total_lines=total,
            content="\n".join(lines[start - 1 : end]),
        )


@dataclass(slots=True, frozen=True)
class GlobDocumentsTool:
    """Find documents matching a glob pattern."""

    path: Path

    def __call__(self, pattern: str) -> list[str]:
        """Find documents matching a glob pattern.

        Args:
            pattern: Glob pattern to match (e.g., "*.md", "notes/*.txt", "**/*.py").
        """
        documents = _load_documents(self.path)
        return [name for name in documents.keys() if fnmatch(name, pattern)]


@dataclass(slots=True, frozen=True)
class GrepTool:
    """Search documents for a pattern."""

    path: Path

    def __call__(
        self,
        pattern: str,
        glob: str | None = None,
        context_lines: int = 0,
        include_content: bool = True,
    ) -> list[GrepMatch]:
        """Search documents for a pattern.

        Uses smart case matching: case-insensitive unless the pattern contains
        uppercase letters.

        Args:
            pattern: Text or regex pattern to search for.
            glob: Only search files matching this pattern (e.g., "*.md", "notes/*").
            context_lines: Number of lines to show before and after each match.
            include_content: Whether to include the matching line content.
        """
        if not self.path.exists():
            return []

        rg = Ripgrepy(pattern, str(self.path)).smart_case()
        if glob:
            rg = rg.glob(glob)
        if context_lines > 0:
            rg = rg.context(context_lines)

        matches: list[GrepMatch] = []
        try:
            for item in rg.run().as_dict:
                if item.get("type") == "match":
                    data = item["data"]
                    filepath = data["path"]["text"]
                    doc_name = str(Path(filepath).relative_to(self.path))
                    content = (
                        data["lines"]["text"].rstrip("\n") if include_content else None
                    )
                    matches.append(
                        GrepMatch(
                            filename=doc_name,
                            line=data["line_number"],
                            content=content,
                        )
                    )
        except Exception:
            pass

        return matches


@dataclass(slots=True, frozen=True)
class SearchDocumentsTool:
    """Semantic search for documents using BM25 ranking."""

    path: Path

    async def __call__(
        self,
        query: str,
        top_k: int = 3,
    ) -> list[RetrievedDocument]:
        """Search documents semantically using BM25 ranking.

        Args:
            query: Natural language search query.
            top_k: Maximum results to return.
        """
        documents = _load_documents(self.path)
        if not documents:
            return []

        index, filenames = create_index(documents)
        results = bm25_search_documents(query, documents, index, filenames, top_k)
        return [
            RetrievedDocument(
                filename=filename,
                content=content,
                score=round(score, 4),
            )
            for filename, content, score in results
        ]


@dataclass(slots=True, frozen=True)
class ListChunksTool:
    """List chunk metadata for a document."""

    path: Path

    def __call__(self, filename: str) -> list[ChunkSummary] | None:
        """List chunk metadata for a document.

        Args:
            filename: The document filename.
        """
        chunked = _load_chunked_document(self.path, filename)
        if not chunked:
            return None
        return [
            ChunkSummary(
                index=c.index,
                token_count=c.token_count,
                start_index=c.start_index,
                end_index=c.end_index,
            )
            for c in chunked.chunks
        ]


@dataclass(slots=True, frozen=True)
class GetChunkTool:
    """Get the text content of a specific chunk."""

    path: Path

    def __call__(
        self,
        filename: str,
        chunk_index: int,
    ) -> str | None:
        """Get the text content of a specific chunk.

        Args:
            filename: The document filename.
            chunk_index: The index of the chunk to retrieve.
        """
        chunked = _load_chunked_document(self.path, filename)
        if not chunked:
            return None
        for chunk in chunked.chunks:
            if chunk.index == chunk_index:
                return chunk.text
        return None


@dataclass(slots=True, frozen=True)
class SearchChunksTool:
    """Search across all chunk files in a directory using BM25 ranking."""

    path: Path

    def __call__(
        self,
        query: str,
        top_k: int = 5,
    ) -> list[RetrievedChunk]:
        """Search across all chunk files using BM25 ranking.

        Args:
            query: Natural language search query.
            top_k: Maximum results to return.
        """
        if not self.path.exists():
            return []

        all_chunks: list[RetrievedChunk] = []
        all_texts: list[str] = []
        for chunk_file in sorted(self.path.glob("*.json")):
            doc_filename = chunk_file.name.removesuffix(".json")
            try:
                data = json.loads(chunk_file.read_text(encoding="utf-8"))
                doc = ChunkedDocument.model_validate(data)
            except Exception:
                continue
            for chunk in doc.chunks:
                all_chunks.append(
                    RetrievedChunk(
                        filename=doc_filename,
                        chunk_index=chunk.index,
                        text=chunk.text,
                        token_count=chunk.token_count,
                        score=0.0,
                    )
                )
                all_texts.append(chunk.text)

        if not all_chunks:
            return []

        corpus_tokens = bm25s.tokenize(all_texts)
        retriever = bm25s.BM25()
        retriever.index(corpus_tokens)
        query_tokens = bm25s.tokenize([query])
        indices, scores = retriever.retrieve(query_tokens, k=min(top_k, len(all_chunks)))

        results: list[RetrievedChunk] = []
        for idx, score in zip(indices[0], scores[0]):
            if idx < len(all_chunks):
                chunk = all_chunks[idx]
                results.append(chunk.model_copy(update={"score": round(float(score), 4)}))
        return results
