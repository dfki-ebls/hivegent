"""Document listing and reading tool callables."""

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from fnmatch import fnmatch
from typing import Annotated, override

from pydantic import Field

from .base import PathsTool, file_allowed, resolve_search_path

__all__ = [
    "DocumentEndLineArg",
    "DocumentFilenameArg",
    "DocumentMaxCharsArg",
    "DocumentMaxDepthArg",
    "DocumentMaxResultsArg",
    "DocumentRange",
    "DocumentStartLineArg",
    "DocumentSubdirArg",
    "DocumentSummary",
    "GetDocumentLinesTool",
    "GetDocumentTool",
    "GlobDocumentsTool",
    "GlobPatternArg",
    "ListDocumentsTool",
]

logger = logging.getLogger(__name__)


@dataclass(slots=True, frozen=True)
class DocumentSummary:
    """Summary of a document or directory."""

    filename: str
    size: int
    modified_at: datetime | None = None
    is_directory: bool = False


@dataclass(slots=True, frozen=True)
class DocumentRange:
    """A range of lines from a document."""

    start_line: int
    end_line: int
    total_lines: int
    content: str


DocumentSubdirArg = Annotated[
    str | None,
    Field(description="Relative subdirectory to limit document listing to."),
]
DocumentMaxDepthArg = Annotated[
    int | None,
    Field(
        description="Maximum nesting depth relative to the selected directory.",
        ge=1,
    ),
]
DocumentFilenameArg = Annotated[
    str,
    Field(description="Relative file path within the tool workspace."),
]
DocumentStartLineArg = Annotated[
    int,
    Field(description="First 1-based line number to include.", ge=1),
]
DocumentEndLineArg = Annotated[
    int | None,
    Field(
        description="Last 1-based line number to include. Defaults to a window of lines from start when omitted.",
        ge=1,
    ),
]
DocumentMaxResultsArg = Annotated[
    int,
    Field(description="Maximum number of entries to return.", ge=1, le=1000),
]
DocumentMaxCharsArg = Annotated[
    int | None,
    Field(
        description=(
            "Maximum number of characters to return. "
            "Content is truncated with a marker if exceeded."
        ),
        ge=1,
    ),
]
GlobPatternArg = Annotated[
    str,
    Field(description="Glob pattern to match against relative document paths."),
]


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
class ListDocumentsTool(PathsTool):
    """List all available documents with their sizes in bytes."""

    glob: str | None = None

    @override
    def __call__(
        self,
        subdir: DocumentSubdirArg = None,
        max_depth: DocumentMaxDepthArg = 1,
        max_results: DocumentMaxResultsArg = 200,
    ) -> list[DocumentSummary]:
        """List all available documents with their sizes in bytes."""
        results: list[DocumentSummary] = []
        for sp in self.resolved_paths:
            if not sp.path.exists():
                continue
            for f in sorted(sp.path.rglob(self.glob or "*")):
                is_dir = f.is_dir()
                if not is_dir and not f.is_file():
                    continue
                rel = str(f.relative_to(sp.path).as_posix())
                if not file_allowed(sp.filter_func, rel):
                    continue
                if not _matches_subdir_and_depth(rel, subdir, max_depth):
                    continue
                stat = f.stat()
                results.append(
                    DocumentSummary(
                        filename=sp.prefixed(rel),
                        size=stat.st_size if not is_dir else 0,
                        modified_at=datetime.fromtimestamp(
                            stat.st_mtime, tz=timezone.utc
                        ),
                        is_directory=is_dir,
                    )
                )
        return results[:max_results]


@dataclass(slots=True, frozen=True)
class GetDocumentTool(PathsTool):
    """Get the full content of a specific document."""

    @override
    def __call__(
        self,
        filename: DocumentFilenameArg,
        max_chars: DocumentMaxCharsArg = 100_000,
    ) -> str | None:
        """Get the full content of a specific document."""
        resolved = resolve_search_path(self.resolved_paths, filename)
        if resolved is None:
            return None
        sp, local = resolved
        if not file_allowed(sp.filter_func, local):
            return None
        file_path = (sp.path / local).resolve()
        if not file_path.is_relative_to(sp.path.resolve()):
            return None
        if not file_path.is_file():
            return None
        content = file_path.read_text(encoding="utf-8")
        if max_chars is not None and len(content) > max_chars:
            content = (
                content[:max_chars]
                + "\n\n[truncated — use get_document_lines for specific sections]"
            )
        return content


@dataclass(slots=True, frozen=True)
class GetDocumentLinesTool(PathsTool):
    """Get a range of lines from a document."""

    default_lines: int = 200

    @override
    def __call__(
        self,
        filename: DocumentFilenameArg,
        start: DocumentStartLineArg = 1,
        end: DocumentEndLineArg = None,
    ) -> DocumentRange | None:
        """Get a range of lines from a document."""
        resolved = resolve_search_path(self.resolved_paths, filename)
        if resolved is None:
            return None
        sp, local = resolved
        if not file_allowed(sp.filter_func, local):
            return None
        file_path = (sp.path / local).resolve()
        if not file_path.is_relative_to(sp.path.resolve()):
            return None
        if not file_path.is_file():
            return None

        lines = file_path.read_text(encoding="utf-8").splitlines()
        total = len(lines)
        start = max(1, start)
        if end is None:
            end = min(total, start + self.default_lines - 1)
        else:
            end = min(total, end)

        return DocumentRange(
            start_line=start,
            end_line=end,
            total_lines=total,
            content="\n".join(lines[start - 1 : end]),
        )


@dataclass(slots=True, frozen=True)
class GlobDocumentsTool(PathsTool):
    """Find documents matching a glob pattern."""

    glob: str | None = None

    @override
    def __call__(
        self,
        pattern: GlobPatternArg,
        max_results: DocumentMaxResultsArg = 200,
    ) -> list[str]:
        """Find documents matching a glob pattern."""
        results: list[str] = []
        for sp in self.resolved_paths:
            if not sp.path.exists():
                continue
            for f in sorted(sp.path.rglob(self.glob or "*")):
                if not f.is_file():
                    continue
                rel = str(f.relative_to(sp.path).as_posix())
                if fnmatch(rel, pattern) and file_allowed(sp.filter_func, rel):
                    results.append(sp.prefixed(rel))
        return results[:max_results]
