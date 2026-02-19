"""Shared path-scoped tool generators used by agent and MCP wrappers."""

import logging
from dataclasses import dataclass
from typing import Literal
from fnmatch import fnmatch
from pathlib import Path

from ripgrepy import Ripgrepy

from .chunks import load_chunked_document, rechunk_document
from .config import DOCUMENT_EXTENSION
from .types import (
    ChunkSummary,
    DocumentFilter,
    DocumentRange,
    DocumentSummary,
    GrepMatch,
    RetrievedChunk,
)

__all__ = [
    "DocumentFilter",
    "EditDocumentTool",
    "GetChunkTool",
    "GetDocumentLinesTool",
    "GetDocumentTool",
    "GlobDocumentsTool",
    "GrepTool",
    "ListChunksTool",
    "ListDocumentsTool",
    "SearchTool",
    "WriteDocumentTool",
]

logger = logging.getLogger(__name__)


def _matches_subdir_and_depth(
    filepath: str,
    subdir: str | None,
    max_depth: int | None,
) -> bool:
    """Check whether *filepath* falls within *subdir* and *max_depth*.

    Args:
        filepath: Document path relative to the document root.
        subdir: If set, only accept paths that start with this prefix.
            Both ``"projects"`` and ``"projects/"`` are accepted.
        max_depth: Maximum nesting depth relative to *subdir* (or root).
            A file directly inside the directory has depth 1.
    """
    if subdir is not None:
        prefix = subdir if subdir.endswith("/") else subdir + "/"
        if not filepath.startswith(prefix):
            return False
        relative = filepath[len(prefix) :]
    else:
        relative = filepath

    if max_depth is not None:
        depth = relative.count("/") + 1
        if depth > max_depth:
            return False

    return True


@dataclass(slots=True, frozen=True)
class ListDocumentsTool:
    """List all available documents with their sizes in bytes."""

    path: Path
    document_filter: DocumentFilter | None = None

    def __call__(
        self,
        subdir: str | None = None,
        max_depth: int | None = None,
    ) -> list[DocumentSummary]:
        """List all available documents with their sizes in bytes.

        Args:
            subdir: Only include documents under this subdirectory.
            max_depth: Maximum nesting depth relative to *subdir* (or root).
        """
        if not self.path.exists():
            return []
        results: list[DocumentSummary] = []
        for f in sorted(self.path.rglob(f"*{DOCUMENT_EXTENSION}")):
            if f.is_file():
                rel = str(f.relative_to(self.path).as_posix())
                results.append(DocumentSummary(filename=rel, size=f.stat().st_size))
        if subdir is not None or max_depth is not None:
            results = [
                r
                for r in results
                if _matches_subdir_and_depth(r.filename, subdir, max_depth)
            ]
        if self.document_filter:
            results = [
                r for r in results if self.document_filter.is_included(r.filename)
            ]
        return results


@dataclass(slots=True, frozen=True)
class GetDocumentTool:
    """Get the full content of a specific document."""

    path: Path
    document_filter: DocumentFilter | None = None

    def __call__(self, filename: str) -> str | None:
        """Get the full content of a specific document.

        Args:
            filename: The exact filename to retrieve.
        """
        if self.document_filter and not self.document_filter.is_included(filename):
            return None
        file_path = (self.path / filename).resolve()
        if not file_path.is_relative_to(self.path.resolve()):
            return None
        if not file_path.is_file():
            return None
        return file_path.read_text(encoding="utf-8")


@dataclass(slots=True, frozen=True)
class GetDocumentLinesTool:
    """Get a range of lines from a document."""

    path: Path
    document_filter: DocumentFilter | None = None

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
        if self.document_filter and not self.document_filter.is_included(filename):
            return None

        file_path = (self.path / filename).resolve()
        if not file_path.is_relative_to(self.path.resolve()):
            return None
        if not file_path.is_file():
            return None

        lines = file_path.read_text(encoding="utf-8").splitlines()
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
    document_filter: DocumentFilter | None = None

    def __call__(self, pattern: str) -> list[str]:
        """Find documents matching a glob pattern.

        Args:
            pattern: Glob pattern to match (e.g., "*.md", "notes/*.txt", "**/*.py").
        """
        if not self.path.exists():
            return []
        results: list[str] = []
        for f in sorted(self.path.rglob(f"*{DOCUMENT_EXTENSION}")):
            if f.is_file():
                rel = str(f.relative_to(self.path).as_posix())
                if fnmatch(rel, pattern):
                    results.append(rel)
        if self.document_filter:
            results = [r for r in results if self.document_filter.is_included(r)]
        return results


@dataclass(slots=True, frozen=True)
class GrepTool:
    """Search documents for a pattern."""

    path: Path
    document_filter: DocumentFilter | None = None

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
            for item in rg.json().run().as_dict:
                if item.get("type") == "match":
                    data = item["data"]
                    filepath = data["path"]["text"]
                    doc_name = str(Path(filepath).relative_to(self.path))
                    if self.document_filter and not self.document_filter.is_included(
                        doc_name
                    ):
                        continue
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
            logger.warning("Grep failed for pattern %r in %s", pattern, self.path)

        return matches


@dataclass(slots=True, frozen=True)
class SearchTool:
    """Dense or sparse chunk search using LanceDB."""

    user_id: str
    search_type: Literal["dense", "sparse"]
    document_filter: DocumentFilter | None = None

    def __call__(
        self,
        query: str,
        top_k: int = 5,
    ) -> list[RetrievedChunk]:
        """Search chunks using the configured search type.

        Args:
            query: Natural language search query.
            top_k: Maximum results to return.
        """
        from .retrieval import parse_chunk_key, search_dense, search_sparse

        search_func = search_dense if self.search_type == "dense" else search_sparse
        results = search_func(
            self.user_id,
            query,
            top_k,
            self.document_filter,
        )
        return [
            RetrievedChunk(
                filename=filename,
                chunk_index=chunk_index,
                text=text,
                token_count=len(text.split()),
                score=round(score, 4),
            )
            for key, text, score in results
            for filename, chunk_index in [parse_chunk_key(key)]
        ]


@dataclass(slots=True, frozen=True)
class ListChunksTool:
    """List chunk metadata for a document."""

    path: Path
    document_filter: DocumentFilter | None = None

    def __call__(self, filename: str) -> list[ChunkSummary] | None:
        """List chunk metadata for a document.

        Args:
            filename: The document filename.
        """
        if self.document_filter and not self.document_filter.is_included(filename):
            return None
        chunked = load_chunked_document(self.path, filename)
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
    document_filter: DocumentFilter | None = None

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
        if self.document_filter and not self.document_filter.is_included(filename):
            return None
        chunked = load_chunked_document(self.path, filename)
        if not chunked:
            return None
        for chunk in chunked.chunks:
            if chunk.index == chunk_index:
                return chunk.text
        return None


@dataclass(slots=True, frozen=True)
class EditDocumentTool:
    """Edit a document by replacing an exact string with a new string."""

    path: Path
    user_id: str
    document_filter: DocumentFilter | None = None

    def __call__(
        self,
        filename: str,
        old_string: str,
        new_string: str,
    ) -> str:
        """Replace an exact string in a document.

        Fails if the string does not exist or appears more than once,
        ensuring unambiguous edits.  On success the document is
        automatically re-chunked and the search index is synced.

        Args:
            filename: The relative document path.
            old_string: The exact text to replace. Must appear exactly once.
            new_string: The replacement text.
        """
        if self.document_filter and not self.document_filter.is_included(filename):
            return f"Error: '{filename}' is not accessible."
        file_path = (self.path / filename).resolve()
        if not file_path.is_relative_to(self.path.resolve()):
            return "Error: path traversal detected."
        if not file_path.is_file():
            return f"Error: '{filename}' does not exist."

        content = file_path.read_text(encoding="utf-8")
        count = content.count(old_string)
        if count == 0:
            return f"Error: old_string not found in '{filename}'."
        if count > 1:
            return (
                f"Error: old_string appears {count} times in '{filename}'; "
                "must be unique."
            )

        new_content = content.replace(old_string, new_string, 1)
        file_path.write_text(new_content, encoding="utf-8")
        rechunk_document(self.user_id, filename)
        return f"Replaced 1 occurrence in '{filename}'."


@dataclass(slots=True, frozen=True)
class WriteDocumentTool:
    """Write content to a document using prepend, append, or replace mode."""

    path: Path
    user_id: str
    document_filter: DocumentFilter | None = None

    def __call__(
        self,
        filename: str,
        content: str,
        mode: Literal["prepend", "append", "replace"] = "replace",
    ) -> str:
        """Write content to a document.

        On success the document is automatically re-chunked and the
        search index is synced.

        Args:
            filename: The relative document path.
            content: The text content to write.
            mode: ``"replace"`` overwrites (creates if absent),
                ``"append"`` adds to the end,
                ``"prepend"`` adds to the start.
        """
        if self.document_filter and not self.document_filter.is_included(filename):
            return f"Error: '{filename}' is not accessible."
        file_path = (self.path / filename).resolve()
        if not file_path.is_relative_to(self.path.resolve()):
            return "Error: path traversal detected."
        if not filename.endswith(DOCUMENT_EXTENSION):
            return f"Error: only '{DOCUMENT_EXTENSION}' files are supported."

        if mode == "replace":
            file_path.parent.mkdir(parents=True, exist_ok=True)
            file_path.write_text(content, encoding="utf-8")
            message = f"Wrote {len(content)} characters to '{filename}'."
        elif not file_path.is_file():
            return f"Error: '{filename}' does not exist (use mode='replace' to create)."
        elif mode == "append":
            existing = file_path.read_text(encoding="utf-8")
            file_path.write_text(existing + content, encoding="utf-8")
            message = f"Appended {len(content)} characters to '{filename}'."
        else:
            existing = file_path.read_text(encoding="utf-8")
            file_path.write_text(content + existing, encoding="utf-8")
            message = f"Prepended {len(content)} characters to '{filename}'."

        rechunk_document(self.user_id, filename)
        return message
