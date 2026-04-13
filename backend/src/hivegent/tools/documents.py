"""Document listing and reading tool callables."""

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from fnmatch import fnmatch
from glob import has_magic as is_glob
from typing import Annotated, override

from pydantic import Field

from .base import SearchPath, SyncPathTool, ToolOutput, file_allowed, resolve_search_path

__all__ = [
    "DocumentFilenameArg",
    "DocumentRange",
    "DocumentSummary",
    "DocumentTreeNode",
    "ListDocumentsTool",
    "ReadDocumentTool",
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


DocumentFilenameArg = Annotated[
    str,
    Field(description="Relative file path within the tool workspace."),
]
DocumentPathArg = Annotated[
    str | None,
    Field(
        description=(
            "Subdirectory to list, or a glob pattern to filter files "
            "(e.g. `reports` or `*.md`)."
        ),
    ),
]
DocumentFlattenArg = Annotated[
    bool,
    Field(
        description=(
            "When true, return a flat list with sizes and dates. "
            "When false, return a hierarchical directory tree."
        ),
    ),
]
DocumentMaxDepthArg = Annotated[
    int | None,
    Field(
        description="Maximum nesting depth relative to the selected directory.",
        ge=1,
    ),
]
DocumentMaxResultsArg = Annotated[
    int,
    Field(description="Maximum number of entries to return.", ge=1, le=1000),
]
DocumentStartLineArg = Annotated[
    int | None,
    Field(description="First 1-based line number to include.", ge=1),
]
DocumentEndLineArg = Annotated[
    int | None,
    Field(
        description="Last 1-based line number to include. Defaults to a window of lines from start when omitted.",
        ge=1,
    ),
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


def _scan_entries(
    resolved_paths: tuple[SearchPath, ...],
    glob: str | None,
    subdir: str | None,
    max_depth: int | None,
    max_results: int,
) -> list[DocumentSummary]:
    """Collect matching file and directory entries from search paths."""
    results: list[DocumentSummary] = []
    for sp in resolved_paths:
        if not sp.path.exists():
            continue
        for f in sorted(sp.path.rglob(glob or "*")):
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


def _scan_glob(
    resolved_paths: tuple[SearchPath, ...],
    base_glob: str | None,
    pattern: str,
    max_results: int,
) -> list[str]:
    """Find files matching a glob *pattern* across search paths."""
    results: list[str] = []
    # When no base_glob restricts the file set, let rglob do the filtering
    # directly instead of enumerating every file and re-filtering with fnmatch.
    effective_glob = pattern if base_glob is None else base_glob
    skip_fnmatch = base_glob is None
    for sp in resolved_paths:
        if not sp.path.exists():
            continue
        for f in sorted(sp.path.rglob(effective_glob)):
            if not f.is_file():
                continue
            rel = str(f.relative_to(sp.path).as_posix())
            if (skip_fnmatch or fnmatch(rel, pattern)) and file_allowed(
                sp.filter_func, rel
            ):
                results.append(sp.prefixed(rel))
    return results[:max_results]


@dataclass(slots=True, frozen=True)
class DocumentTreeNode:
    """A file or directory node in a document tree."""

    name: str
    path: str
    is_directory: bool = False
    size: int = 0
    children: tuple["DocumentTreeNode", ...] = ()


@dataclass
class _TreeBuildNode:
    """Mutable intermediate node used while constructing the tree."""

    entry: DocumentSummary | None = None
    children: dict[str, "_TreeBuildNode"] = field(default_factory=dict)


def _build_document_tree(entries: list[DocumentSummary]) -> DocumentTreeNode:
    """Build a :class:`DocumentTreeNode` tree from a flat list of entries."""
    root = _TreeBuildNode()
    for entry in entries:
        node = root
        for part in entry.filename.split("/"):
            if part not in node.children:
                node.children[part] = _TreeBuildNode()
            node = node.children[part]
        node.entry = entry

    def _convert(name: str, path: str, build: _TreeBuildNode) -> DocumentTreeNode:
        children: list[DocumentTreeNode] = []
        for key in sorted(build.children):
            child_path = f"{path}/{key}" if path else key
            children.append(_convert(key, child_path, build.children[key]))
        entry = build.entry
        return DocumentTreeNode(
            name=name,
            path=path,
            is_directory=entry.is_directory if entry else bool(children),
            size=entry.size if entry and not entry.is_directory else 0,
            children=tuple(children),
        )

    return _convert(".", "", root)


def _format_document_tree(
    node: DocumentTreeNode,
    prefix: str = "",
    is_last: bool = True,
) -> list[str]:
    """Format a :class:`DocumentTreeNode` as ``tree(1)``-style text."""
    lines: list[str] = []
    if node.path:
        connector = "└── " if is_last else "├── "
        suffix = "/" if node.is_directory else ""
        size_str = f" ({_humanize_size(node.size)})" if not node.is_directory else ""
        lines.append(f"{prefix}{connector}{node.name}{suffix}{size_str}")
    child_prefix = prefix + ("    " if is_last else "│   ") if node.path else ""
    for i, child in enumerate(node.children):
        lines.extend(
            _format_document_tree(child, child_prefix, i == len(node.children) - 1)
        )
    return lines


@dataclass(slots=True, frozen=True)
class ListDocumentsTool(SyncPathTool[list[DocumentSummary] | DocumentTreeNode | list[str]]):
    """List available documents, optionally as a tree or filtered by glob pattern."""

    glob: str | None = None

    @override
    def __call__(
        self,
        path: DocumentPathArg = None,
        flatten: DocumentFlattenArg = True,
        max_depth: DocumentMaxDepthArg = 1,
        max_results: DocumentMaxResultsArg = 200,
    ) -> ToolOutput[list[DocumentSummary] | DocumentTreeNode | list[str]]:
        """List available documents with sizes and dates.

        Set ``flatten=False`` to show a hierarchical directory tree.
        The ``path`` parameter accepts a subdirectory name or a glob
        pattern (e.g. ``*.md``) to filter results.
        """
        if path is not None and is_glob(path):
            results = _scan_glob(
                self.resolved_paths, self.glob, path, max_results
            )
            return ToolOutput(
                data=results,
                formatted="\n".join(results) if results else "(no matches)",
            )

        subdir = path

        if flatten:
            results = _scan_entries(
                self.resolved_paths, self.glob, subdir, max_depth, max_results
            )
            if not results:
                return ToolOutput(data=results, formatted="(no documents)")
            lines: list[str] = []
            for d in results:
                date = d.modified_at.strftime("%Y-%m-%d %H:%M") if d.modified_at else "-"
                kind = "d" if d.is_directory else "-"
                lines.append(f"{kind} {date}  {_humanize_size(d.size):>6}  {d.filename}")
            return ToolOutput(data=results, formatted="\n".join(lines))

        entries = _scan_entries(
            self.resolved_paths, self.glob, subdir, max_depth, max_results
        )
        root = _build_document_tree(entries)
        tree_lines = _format_document_tree(root)
        if not tree_lines:
            return ToolOutput(data=root, formatted="(empty)")

        dir_count = sum(1 for e in entries if e.is_directory)
        file_count = sum(1 for e in entries if not e.is_directory)
        tree_lines.append("")
        tree_lines.append(
            f"{dir_count} {'directory' if dir_count == 1 else 'directories'}, "
            f"{file_count} {'file' if file_count == 1 else 'files'}"
        )
        return ToolOutput(data=root, formatted="\n".join(tree_lines))


@dataclass(slots=True, frozen=True)
class ReadDocumentTool(SyncPathTool[str | DocumentRange | None]):
    """Read the content of a document, optionally limited to a line range."""

    max_chars: int = 100_000
    default_lines: int = 200

    @override
    def __call__(
        self,
        filename: DocumentFilenameArg,
        start_line: DocumentStartLineArg = None,
        end_line: DocumentEndLineArg = None,
    ) -> ToolOutput[str | DocumentRange | None]:
        """Read a document's content.

        Returns the full content when no line range is given,
        or a specific range of lines when ``start_line`` is provided.
        Use ``start_line`` and ``end_line`` to read specific sections
        of large documents.
        """
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

        if start_line is not None:
            all_lines = file_path.read_text(encoding="utf-8").splitlines()
            total = len(all_lines)
            start = max(1, start_line)
            if end_line is None:
                end = min(total, start + self.default_lines - 1)
            else:
                end = min(total, end_line)

            result = DocumentRange(
                start_line=start,
                end_line=end,
                total_lines=total,
                content="\n".join(all_lines[start - 1 : end]),
            )
            annotated = "\n".join(
                f"{start + i}: {line}"
                for i, line in enumerate(all_lines[start - 1 : end])
            )
            return ToolOutput(
                data=result,
                formatted=f"lines {result.start_line}-{result.end_line} of {result.total_lines}:\n{annotated}",
            )

        content = file_path.read_text(encoding="utf-8")
        if len(content) > self.max_chars:
            content = (
                content[: self.max_chars]
                + "\n\n[truncated — use read_document with start_line/end_line for specific sections]"
            )
        return ToolOutput(data=content)
