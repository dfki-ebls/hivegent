"""Document listing and reading tool callables."""

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from fnmatch import fnmatch
from typing import Annotated, override

from pydantic import Field

from .base import SyncPathTool, ToolOutput, file_allowed, resolve_search_path

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

_NOT_FOUND_MSG = "(document not found)"

_SIZE_UNITS = ("B", "K", "M", "G")


def _humanize_size(n: int) -> str:
    """Format byte count as a compact human-readable string."""
    value = float(n)
    for unit in _SIZE_UNITS[:-1]:
        if abs(value) < 1024:
            return f"{value:.0f}{unit}" if value == int(value) else f"{value:.1f}{unit}"
        value /= 1024
    return f"{value:.1f}{_SIZE_UNITS[-1]}"


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
class ListDocumentsTool(SyncPathTool[list[DocumentSummary]]):
    """List all available documents with their sizes in bytes."""

    glob: str | None = None

    @override
    def __call__(
        self,
        subdir: DocumentSubdirArg = None,
        max_depth: DocumentMaxDepthArg = 1,
        max_results: DocumentMaxResultsArg = 200,
    ) -> ToolOutput[list[DocumentSummary]]:
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
        results = results[:max_results]
        if not results:
            return ToolOutput(data=results, formatted="(no documents)")
        lines: list[str] = []
        for d in results:
            date = d.modified_at.strftime("%Y-%m-%d %H:%M") if d.modified_at else "-"
            kind = "d" if d.is_directory else "-"
            lines.append(f"{kind} {date}  {_humanize_size(d.size):>6}  {d.filename}")
        return ToolOutput(data=results, formatted="\n".join(lines))


@dataclass(slots=True, frozen=True)
class GetDocumentTool(SyncPathTool[str | None]):
    """Get the full content of a specific document."""

    @override
    def __call__(
        self,
        filename: DocumentFilenameArg,
        max_chars: DocumentMaxCharsArg = 100_000,
    ) -> ToolOutput[str | None]:
        """Get the full content of a specific document."""
        resolved = resolve_search_path(self.resolved_paths, filename)
        if resolved is None:
            return ToolOutput(data=None)
        sp, local = resolved
        if not file_allowed(sp.filter_func, local):
            return ToolOutput(data=None)
        file_path = (sp.path / local).resolve()
        if not file_path.is_relative_to(sp.path.resolve()):
            return ToolOutput(data=None)
        if not file_path.is_file():
            return ToolOutput(data=None)
        content = file_path.read_text(encoding="utf-8")
        if max_chars is not None and len(content) > max_chars:
            content = (
                content[:max_chars]
                + "\n\n[truncated — use get_document_lines for specific sections]"
            )
        return ToolOutput(data=content)


@dataclass(slots=True, frozen=True)
class GetDocumentLinesTool(SyncPathTool[DocumentRange | None]):
    """Get a range of lines from a document."""

    default_lines: int = 200

    @override
    def __call__(
        self,
        filename: DocumentFilenameArg,
        start: DocumentStartLineArg = 1,
        end: DocumentEndLineArg = None,
    ) -> ToolOutput[DocumentRange | None]:
        """Get a range of lines from a document."""
        resolved = resolve_search_path(self.resolved_paths, filename)
        if resolved is None:
            return ToolOutput(data=None, formatted=_NOT_FOUND_MSG)
        sp, local = resolved
        if not file_allowed(sp.filter_func, local):
            return ToolOutput(data=None, formatted=_NOT_FOUND_MSG)
        file_path = (sp.path / local).resolve()
        if not file_path.is_relative_to(sp.path.resolve()):
            return ToolOutput(data=None, formatted=_NOT_FOUND_MSG)
        if not file_path.is_file():
            return ToolOutput(data=None, formatted=_NOT_FOUND_MSG)

        lines = file_path.read_text(encoding="utf-8").splitlines()
        total = len(lines)
        start = max(1, start)
        if end is None:
            end = min(total, start + self.default_lines - 1)
        else:
            end = min(total, end)

        result = DocumentRange(
            start_line=start,
            end_line=end,
            total_lines=total,
            content="\n".join(lines[start - 1 : end]),
        )
        return ToolOutput(
            data=result,
            formatted=f"lines {result.start_line}-{result.end_line} of {result.total_lines}:\n{result.content}",
        )


@dataclass(slots=True, frozen=True)
class GlobDocumentsTool(SyncPathTool[list[str]]):
    """Find documents matching a glob pattern."""

    glob: str | None = None

    @override
    def __call__(
        self,
        pattern: GlobPatternArg,
        max_results: DocumentMaxResultsArg = 200,
    ) -> ToolOutput[list[str]]:
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
        results = results[:max_results]
        return ToolOutput(
            data=results,
            formatted="\n".join(results) if results else "(no matches)",
        )
