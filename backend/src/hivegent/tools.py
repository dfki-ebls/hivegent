"""Generic path-scoped tool callables for document operations."""

import json
import logging
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from fnmatch import fnmatch
from pathlib import Path
from typing import Literal

from .subprocesses import jq_filter, rg_search
from .types import (
    ChunkSummary,
    DocumentRange,
    DocumentSummary,
    GrepMatch,
    RetrievedChunk,
)

__all__ = [
    "EditDocumentTool",
    "GetChunkTool",
    "GetDocumentLinesTool",
    "GetDocumentTool",
    "GlobDocumentsTool",
    "GrepTool",
    "JqTool",
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
    extension: str = ".md"
    document_filter: Callable[[str], bool] | None = None

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
        pattern = f"*{self.extension}" if self.extension else "*"
        for f in sorted(self.path.rglob(pattern)):
            if f.is_file():
                rel = str(f.relative_to(self.path).as_posix())
                stat = f.stat()
                results.append(DocumentSummary(
                    filename=rel,
                    size=stat.st_size,
                    modified_at=datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc),
                ))
        if subdir is not None or max_depth is not None:
            results = [
                r
                for r in results
                if _matches_subdir_and_depth(r.filename, subdir, max_depth)
            ]
        if self.document_filter:
            results = [r for r in results if self.document_filter(r.filename)]
        return results


@dataclass(slots=True, frozen=True)
class GetDocumentTool:
    """Get the full content of a specific document."""

    path: Path
    document_filter: Callable[[str], bool] | None = None

    def __call__(self, filename: str) -> str | None:
        """Get the full content of a specific document.

        Args:
            filename: The exact filename to retrieve.
        """
        if self.document_filter and not self.document_filter(filename):
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
    document_filter: Callable[[str], bool] | None = None

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
        if self.document_filter and not self.document_filter(filename):
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
    extension: str = ".md"
    document_filter: Callable[[str], bool] | None = None

    def __call__(self, pattern: str) -> list[str]:
        """Find documents matching a glob pattern.

        Args:
            pattern: Glob pattern to match (e.g., "*.md", "notes/*.txt", "**/*.py").
        """
        if not self.path.exists():
            return []
        results: list[str] = []
        ext_glob = f"*{self.extension}" if self.extension else "*"
        for f in sorted(self.path.rglob(ext_glob)):
            if f.is_file():
                rel = str(f.relative_to(self.path).as_posix())
                if fnmatch(rel, pattern):
                    results.append(rel)
        if self.document_filter:
            results = [r for r in results if self.document_filter(r)]
        return results


@dataclass(slots=True, frozen=True)
class GrepTool:
    """Search documents for a pattern."""

    path: Path
    document_filter: Callable[[str], bool] | None = None

    async def __call__(
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

        matches: list[GrepMatch] = []
        try:
            for rg_match in await rg_search(
                pattern,
                self.path,
                glob=glob,
                context_lines=context_lines,
            ):
                doc_name = str(Path(rg_match.path).relative_to(self.path))
                if self.document_filter and not self.document_filter(doc_name):
                    continue
                content = rg_match.line_text if include_content else None
                matches.append(
                    GrepMatch(
                        filename=doc_name,
                        line=rg_match.line_number,
                        content=content,
                    )
                )
        except Exception:
            logger.warning("Grep failed for pattern %r in %s", pattern, self.path)

        return matches


@dataclass(slots=True, frozen=True)
class SearchTool:
    """Search chunks using a configured search backend."""

    search_fn: Callable[[str, int], list[RetrievedChunk]]

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
        return self.search_fn(query, top_k)


@dataclass(slots=True, frozen=True)
class ListChunksTool:
    """List chunk metadata for a document."""

    loader: Callable[[str], Sequence[ChunkSummary] | None]
    document_filter: Callable[[str], bool] | None = None

    def __call__(self, filename: str) -> list[ChunkSummary] | None:
        """List chunk metadata for a document.

        Args:
            filename: The document filename.
        """
        if self.document_filter and not self.document_filter(filename):
            return None
        result = self.loader(filename)
        return list(result) if result is not None else None


@dataclass(slots=True, frozen=True)
class GetChunkTool:
    """Get the text content of a specific chunk."""

    loader: Callable[[str, int], str | None]
    document_filter: Callable[[str], bool] | None = None

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
        if self.document_filter and not self.document_filter(filename):
            return None
        return self.loader(filename, chunk_index)


@dataclass(slots=True, frozen=True)
class EditDocumentTool:
    """Edit a document by replacing an exact string with a new string."""

    path: Path
    document_filter: Callable[[str], bool] | None = None
    on_write: Callable[[str], Awaitable[object]] | None = None

    async def __call__(
        self,
        filename: str,
        old_string: str,
        new_string: str,
    ) -> str:
        """Replace an exact string in a document.

        Fails if the string does not exist or appears more than once,
        ensuring unambiguous edits.

        Args:
            filename: The relative document path.
            old_string: The exact text to replace. Must appear exactly once.
            new_string: The replacement text.
        """
        if self.document_filter and not self.document_filter(filename):
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
        if self.on_write:
            await self.on_write(filename)
        return f"Replaced 1 occurrence in '{filename}'."


@dataclass(slots=True, frozen=True)
class WriteDocumentTool:
    """Write content to a document using prepend, append, or replace mode."""

    path: Path
    extension: str = ".md"
    document_filter: Callable[[str], bool] | None = None
    on_write: Callable[[str], Awaitable[object]] | None = None

    async def __call__(
        self,
        filename: str,
        content: str,
        mode: Literal["prepend", "append", "replace"] = "replace",
    ) -> str:
        """Write content to a document.

        Args:
            filename: The relative document path.
            content: The text content to write.
            mode: ``"replace"`` overwrites (creates if absent),
                ``"append"`` adds to the end,
                ``"prepend"`` adds to the start.
        """
        if self.document_filter and not self.document_filter(filename):
            return f"Error: '{filename}' is not accessible."
        file_path = (self.path / filename).resolve()
        if not file_path.is_relative_to(self.path.resolve()):
            return "Error: path traversal detected."
        if self.extension and not filename.endswith(self.extension):
            return f"Error: only '{self.extension}' files are supported."

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

        if self.on_write:
            await self.on_write(filename)
        return message


@dataclass(slots=True, frozen=True)
class JqTool:
    """Run a jq filter against a JSON file."""

    path: Path

    async def __call__(self, filter: str, filename: str) -> str:
        """Run a jq filter expression against a JSON file.

        Args:
            filter: A jq filter expression.
            filename: The file to query.
        """
        file_path = (self.path / filename).resolve()
        if not file_path.is_relative_to(self.path.resolve()):
            return "Error: path traversal detected."
        if not file_path.is_file():
            return f"Error: file '{filename}' not found."
        data = json.loads(file_path.read_text(encoding="utf-8"))

        try:
            result = await jq_filter(filter, data)
        except ValueError as exc:
            return f"Error: {exc}"
        return json.dumps(result, default=str)
