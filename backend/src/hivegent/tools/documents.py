"""Document listing and reading tool callables."""

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from fnmatch import fnmatch
from pathlib import Path
from typing import Annotated, override

from pydantic import Field

from .base import Tool

__all__ = [
    "DocumentEndLineArg",
    "DocumentFilenameArg",
    "DocumentRange",
    "DocumentMaxDepthArg",
    "DocumentSummary",
    "DocumentStartLineArg",
    "DocumentSubdirArg",
    "GetDocumentLinesTool",
    "GetDocumentTool",
    "GlobPatternArg",
    "GlobDocumentsTool",
    "ListDocumentsTool",
]

logger = logging.getLogger(__name__)


@dataclass(slots=True, frozen=True)
class DocumentSummary:
    """Summary of a document."""

    filename: str
    size: int
    modified_at: datetime | None = None


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
    Field(description="Last 1-based line number to include.", ge=1),
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
class ListDocumentsTool(Tool):
    """List all available documents with their sizes in bytes."""

    path: Path
    extension: str = ".md"

    @override
    def __call__(
        self,
        subdir: DocumentSubdirArg = None,
        max_depth: DocumentMaxDepthArg = None,
    ) -> list[DocumentSummary]:
        """List all available documents with their sizes in bytes."""
        if not self.path.exists():
            return []
        results: list[DocumentSummary] = []
        pattern = f"*{self.extension}" if self.extension else "*"
        for f in sorted(self.path.rglob(pattern)):
            if f.is_file():
                rel = str(f.relative_to(self.path).as_posix())
                stat = f.stat()
                results.append(
                    DocumentSummary(
                        filename=rel,
                        size=stat.st_size,
                        modified_at=datetime.fromtimestamp(
                            stat.st_mtime, tz=timezone.utc
                        ),
                    )
                )
        if subdir is not None or max_depth is not None:
            results = [
                r
                for r in results
                if _matches_subdir_and_depth(r.filename, subdir, max_depth)
            ]
        return results


@dataclass(slots=True, frozen=True)
class GetDocumentTool(Tool):
    """Get the full content of a specific document."""

    path: Path

    @override
    def __call__(self, filename: DocumentFilenameArg) -> str | None:
        """Get the full content of a specific document."""
        file_path = (self.path / filename).resolve()
        if not file_path.is_relative_to(self.path.resolve()):
            return None
        if not file_path.is_file():
            return None
        return file_path.read_text(encoding="utf-8")


@dataclass(slots=True, frozen=True)
class GetDocumentLinesTool(Tool):
    """Get a range of lines from a document."""

    path: Path

    @override
    def __call__(
        self,
        filename: DocumentFilenameArg,
        start: DocumentStartLineArg = 1,
        end: DocumentEndLineArg = None,
    ) -> DocumentRange | None:
        """Get a range of lines from a document."""
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
class GlobDocumentsTool(Tool):
    """Find documents matching a glob pattern."""

    path: Path
    extension: str = ".md"

    @override
    def __call__(self, pattern: GlobPatternArg) -> list[str]:
        """Find documents matching a glob pattern."""
        if not self.path.exists():
            return []
        results: list[str] = []
        ext_glob = f"*{self.extension}" if self.extension else "*"
        for f in sorted(self.path.rglob(ext_glob)):
            if f.is_file():
                rel = str(f.relative_to(self.path).as_posix())
                if fnmatch(rel, pattern):
                    results.append(rel)
        return results
