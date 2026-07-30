"""Document listing, globbing, and reading tool callables."""

import logging
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from fnmatch import fnmatch
from pathlib import Path
from typing import Annotated, override

from pydantic import Field

from ..config import content_hash
from ..entries import (
    description_path_for_stem,
    is_assets_dir,
    is_description_file,
    stem_path_from_reference,
)
from ..text import NOT_TEXT_REASON, read_text_file
from .base import (
    WORKSPACE_PATH_HINT,
    WORKSPACE_SCOPE_HINT,
    IncludeIgnoredArg,
    SearchPath,
    SyncPathTool,
    ToolOutput,
    ToolRetry,
    excluded_dirs,
    file_allowed,
    is_in_excluded_dir,
    resolve_accessible_file,
)
from .binary import binary_media_type
from .formatting import annotate_lines

__all__ = [
    "DocumentFilePathArg",
    "DocumentFlattenArg",
    "DocumentLimitArg",
    "DocumentMaxDepthArg",
    "DocumentMaxResultsArg",
    "DocumentOffsetArg",
    "DocumentPathArg",
    "DocumentRange",
    "DocumentSummary",
    "DocumentTreeNode",
    "GlobDocumentsTool",
    "GlobMaxResultsArg",
    "GlobPatternArg",
    "ListDocumentsTool",
    "ReadDocumentTool",
]

logger = logging.getLogger(__name__)

_SIZE_UNITS = ("B", "K", "M", "G")


def _humanize_size(n: int) -> str:
    """Format byte count as a compact human-readable string."""
    value = float(n)
    for unit in _SIZE_UNITS[:-1]:
        if abs(value) < 1024:
            return f"{value:.0f}{unit}" if value == int(value) else f"{value:.1f}{unit}"
        value /= 1024
    return f"{value:.1f}{_SIZE_UNITS[-1]}"


def _pluralize(count: int, singular: str, plural: str) -> str:
    """Return *singular* when *count* is 1, else *plural*."""
    return singular if count == 1 else plural


def _ignored_hint(hidden_count: int) -> str:
    """Nudge toward ``include_ignored`` when hidden entries exist.

    Appended to empty-result messages so a caller who sees nothing knows
    whether the scope is genuinely empty or just filtered.  *hidden_count*
    is how many entries (``.assets`` contents and build/vendor directories)
    the ``include_ignored`` flag would expose; the hint is suppressed when
    nothing is hidden.
    """
    if hidden_count <= 0:
        return ""
    noun = _pluralize(hidden_count, "entry", "entries")
    return (
        f" — {hidden_count} hidden {noun} (`.assets` contents and common "
        f"build/vendor directories); pass include_ignored=True to reveal them"
    )


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
    content_hash: str
    """Fingerprint of the *full* document, for ``expected_hash`` on a later edit."""


DocumentFilePathArg = Annotated[
    str,
    Field(description=f"Path of the document to operate on. {WORKSPACE_PATH_HINT}"),
]
DocumentPathArg = Annotated[
    str | None,
    Field(
        description=(
            f"Optional subdirectory to scope the operation within. "
            f"{WORKSPACE_SCOPE_HINT} Omit to cover them all."
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
DocumentOffsetArg = Annotated[
    int,
    Field(
        description="1-based starting line number.",
        ge=1,
    ),
]
DocumentLimitArg = Annotated[
    int | None,
    Field(
        description=(
            "Maximum number of lines to return. When omitted, uses the tool's "
            "default window."
        ),
        ge=1,
    ),
]

GlobPatternArg = Annotated[
    str,
    Field(
        description=(
            "Glob pattern matched against workspace-relative filenames (e.g. "
            "`*.md`, `**/*.txt`). To restrict to one workspace, prefix the "
            "`path` argument rather than the pattern."
        ),
    ),
]
GlobMaxResultsArg = Annotated[
    int,
    Field(description="Maximum number of matching files to return.", ge=1, le=1000),
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


def _walk_entries(
    resolved_paths: tuple[SearchPath, ...],
    base_glob: str | None,
    exclude_dirs: tuple[str, ...],
    *,
    root_subpath: str | None = None,
    include_dirs: bool,
) -> Iterator[tuple[SearchPath, Path, str, bool]]:
    """Walk search paths yielding ``(sp, absolute, relative, is_dir)`` tuples.

    Applies ``base_glob``, the excluded-dir filter, and the search path's
    own ``filter_func``.  Callers handle sorting, capping, and any
    additional filters (subdir, depth, fnmatch).
    """
    for sp in resolved_paths:
        root = sp.path / root_subpath if root_subpath else sp.path
        if not root.exists():
            continue
        for absolute in sorted(root.rglob(base_glob or "*")):
            is_dir = absolute.is_dir()
            if is_dir and not include_dirs:
                continue
            if not is_dir and not absolute.is_file():
                continue
            rel = str(absolute.relative_to(sp.path).as_posix())
            if is_in_excluded_dir(rel, exclude_dirs):
                continue
            # Elements of `.assets` payload directories pollute the context
            # (a single converted document can carry hundreds of extracted
            # images), so only the directory itself is listed; like the
            # build/vendor dirs, ``include_ignored=True`` reveals them.
            if exclude_dirs and any(
                is_assets_dir(part) for part in rel.split("/")[:-1]
            ):
                continue
            if not file_allowed(sp.filter_func, rel):
                continue
            yield sp, absolute, rel, is_dir


def _scan_entries(
    resolved_paths: tuple[SearchPath, ...],
    base_glob: str | None,
    subdir: str | None,
    max_depth: int | None,
    max_results: int,
    exclude_dirs: tuple[str, ...],
) -> list[DocumentSummary]:
    """Collect matching file and directory entries from search paths."""
    results: list[DocumentSummary] = []
    for sp, absolute, rel, is_dir in _walk_entries(
        resolved_paths, base_glob, exclude_dirs, include_dirs=True
    ):
        if not _matches_subdir_and_depth(rel, subdir, max_depth):
            continue
        stat = absolute.stat()
        results.append(
            DocumentSummary(
                filename=sp.prefixed(rel),
                size=stat.st_size if not is_dir else 0,
                modified_at=datetime.fromtimestamp(stat.st_mtime, tz=UTC),
                is_directory=is_dir,
            )
        )
        if len(results) >= max_results:
            break
    return results


def _glob_entries(
    resolved_paths: tuple[SearchPath, ...],
    base_glob: str | None,
    pattern: str,
    subdir: str | None,
    max_results: int,
    exclude_dirs: tuple[str, ...],
) -> list[str]:
    """Find files matching *pattern* across search paths, scoped to *subdir*."""
    # Without a base_glob, pass the user pattern straight to rglob and skip the
    # per-entry fnmatch pass.
    effective_glob = pattern if base_glob is None else base_glob
    skip_fnmatch = base_glob is None
    results: list[str] = []
    for sp, _absolute, rel, _is_dir in _walk_entries(
        resolved_paths,
        effective_glob,
        exclude_dirs,
        root_subpath=subdir,
        include_dirs=False,
    ):
        if skip_fnmatch or fnmatch(rel, pattern):
            results.append(sp.prefixed(rel))
            if len(results) >= max_results:
                break
    return results


@dataclass(slots=True, frozen=True)
class DocumentTreeNode:
    """A file or directory node in a document tree."""

    name: str
    path: str
    is_directory: bool = False
    size: int = 0
    children: tuple["DocumentTreeNode", ...] = ()


@dataclass(slots=True)
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
class ListDocumentsTool(SyncPathTool[list[DocumentSummary] | DocumentTreeNode]):
    """List available documents as a flat list or hierarchical tree."""

    glob: str | None = None

    @override
    def __call__(
        self,
        path: DocumentPathArg = None,
        flatten: DocumentFlattenArg = True,
        max_depth: DocumentMaxDepthArg = 1,
        max_results: DocumentMaxResultsArg = 200,
        include_ignored: IncludeIgnoredArg = False,
    ) -> ToolOutput[list[DocumentSummary] | DocumentTreeNode]:
        """List available documents with sizes and dates.

        Set ``flatten=False`` to show a hierarchical directory tree.
        Use ``glob_documents`` for pattern-based file matching.  Common
        build and vendor directories and the contents of ``.assets``
        payload directories are skipped by default; pass
        ``include_ignored=True`` to include them.
        """
        exclude = excluded_dirs(include_ignored)
        paths, subdir = self.scoped(path)

        if flatten:
            results = _scan_entries(
                paths,
                self.glob,
                subdir,
                max_depth,
                max_results,
                exclude,
            )
            if not results:
                hint = _ignored_hint(
                    self._hidden_count(
                        paths, subdir, max_depth, max_results, include_ignored
                    )
                )
                return ToolOutput(data=results, formatted=f"(no documents{hint})")
            lines: list[str] = []
            for d in results:
                date = (
                    d.modified_at.strftime("%Y-%m-%d %H:%M") if d.modified_at else "-"
                )
                kind = "d" if d.is_directory else "-"
                lines.append(
                    f"{kind} {date}  {_humanize_size(d.size):>6}  {d.filename}"
                )
            return ToolOutput(data=results, formatted="\n".join(lines))

        entries = _scan_entries(
            paths,
            self.glob,
            subdir,
            max_depth,
            max_results,
            exclude,
        )
        root = _build_document_tree(entries)
        tree_lines = _format_document_tree(root)
        if not tree_lines:
            hint = _ignored_hint(
                self._hidden_count(
                    paths, subdir, max_depth, max_results, include_ignored
                )
            )
            return ToolOutput(data=root, formatted=f"(empty{hint})")

        dir_count = sum(1 for e in entries if e.is_directory)
        file_count = sum(1 for e in entries if not e.is_directory)
        tree_lines.append("")
        tree_lines.append(
            f"{dir_count} {_pluralize(dir_count, 'directory', 'directories')}, "
            f"{file_count} {_pluralize(file_count, 'file', 'files')}"
        )
        return ToolOutput(data=root, formatted="\n".join(tree_lines))

    def _hidden_count(
        self,
        paths: tuple[SearchPath, ...],
        subdir: str | None,
        max_depth: DocumentMaxDepthArg,
        max_results: int,
        include_ignored: bool,
    ) -> int:
        """Count entries an unfiltered scan would expose, for the empty hint.

        Only meaningful when the filtered scan came back empty: every entry
        an exclusion-free scan finds is one the default filter hid.  Returns
        0 when *include_ignored* is set, since nothing is being hidden.
        """
        if include_ignored:
            return 0
        return len(_scan_entries(paths, self.glob, subdir, max_depth, max_results, ()))


@dataclass(slots=True, frozen=True)
class GlobDocumentsTool(SyncPathTool[list[str]]):
    """Find documents whose filenames match a glob pattern."""

    glob: str | None = None

    @override
    def __call__(
        self,
        pattern: GlobPatternArg,
        path: DocumentPathArg = None,
        max_results: GlobMaxResultsArg = 1000,
        include_ignored: IncludeIgnoredArg = False,
    ) -> ToolOutput[list[str]]:
        """Find document filenames matching a glob pattern.

        Returns a flat list of relative filenames.  Use ``list_documents``
        for directory listings with sizes, dates, or tree output.  Common
        build and vendor directories and the contents of ``.assets``
        payload directories are skipped by default; pass
        ``include_ignored=True`` to include them.
        """
        paths, subdir = self.scoped(path)
        results = _glob_entries(
            paths,
            self.glob,
            pattern,
            subdir,
            max_results,
            excluded_dirs(include_ignored),
        )
        if results:
            return ToolOutput(data=results, formatted="\n".join(results))
        hidden = (
            0
            if include_ignored
            else len(_glob_entries(paths, self.glob, pattern, subdir, max_results, ()))
        )
        return ToolOutput(
            data=results, formatted=f"(no matches{_ignored_hint(hidden)})"
        )


@dataclass(slots=True, frozen=True)
class ReadDocumentTool(SyncPathTool[DocumentRange]):
    """Read a document's content as a line range with line numbers.

    ``max_line_chars`` truncates each numbered line in the formatted
    output so a single very long line — a base64-embedded image, a
    minified bundle — cannot flood the model context, while ``max_chars``
    bounds the window as a whole.  The structured ``content`` keeps the
    untruncated lines for the frontend.
    """

    default_lines: int = 2000
    max_chars: int = 100_000
    max_line_chars: int = 2000

    @override
    def __call__(
        self,
        file_path: DocumentFilePathArg,
        offset: DocumentOffsetArg = 1,
        limit: DocumentLimitArg = None,
    ) -> ToolOutput[DocumentRange]:
        """Read a document's content.

        Returns the lines from ``offset`` (1-indexed) up to ``limit`` lines,
        each prefixed with its line number.  When ``limit`` is omitted the
        tool reads a default window and reports how many lines remain so
        the caller can issue a follow-up with a higher ``offset``.  The
        output is also clamped by a per-call character budget.  A file
        stored in a legacy encoding is decoded transparently, with the
        source encoding named next to the hash.
        """
        resolved = resolve_accessible_file(self.resolved_paths, file_path)
        if resolved is None or not resolved[2].is_file():
            raise ToolRetry(f"'{file_path}' not found.")
        _sp, _local, absolute = resolved

        # Reads are uniform: the requested file is read as text and never
        # silently swapped for another.  Non-markdown inputs are redirected only
        # through the error message — a vision-capable binary (image, PDF, video)
        # goes to read_binary_document (some models ingest those natively, and
        # PDFs get custom page rendering), while any non-markdown original's
        # extracted text stays reachable by requesting its ``<stem>.md`` sidecar,
        # whose read then re-runs the same containment checks.
        sidecar_hint = ""
        if not is_description_file(file_path):
            sidecar = description_path_for_stem(stem_path_from_reference(file_path))
            sidecar_hint = f" To read its extracted text, request '{sidecar}' instead."

        media_type = binary_media_type(file_path)
        if media_type is not None:
            raise ToolRetry(
                f"'{file_path}' is a {media_type} binary — use read_binary_document "
                f"to send it to a vision model.{sidecar_hint}"
            )

        # Legacy encodings are decoded rather than refused, and the encoding is
        # reported below: the same seam the upload pipeline and the editing
        # tools use, so a hash taken here still matches on a later edit.
        decoded = read_text_file(absolute)
        if decoded is None:
            raise ToolRetry(f"'{file_path}' {NOT_TEXT_REASON}.{sidecar_hint}")
        file_hash = content_hash(decoded.text)
        all_lines = decoded.text.splitlines()
        total = len(all_lines)
        start = max(1, offset)
        if total == 0:
            empty = DocumentRange(
                start_line=start,
                end_line=start - 1,
                total_lines=0,
                content="",
                content_hash=file_hash,
            )
            return ToolOutput(data=empty, formatted="(empty file)")
        if start > total:
            past_eof = DocumentRange(
                start_line=start,
                end_line=start - 1,
                total_lines=total,
                content="",
                content_hash=file_hash,
            )
            return ToolOutput(
                data=past_eof,
                formatted=f"(offset {start} is past end of file with {total} lines)",
            )

        window = limit if limit is not None else self.default_lines
        end = min(total, start + window - 1)

        # Cap by character budget: one giant line still gets returned alone so
        # the caller sees something rather than an empty range.
        selected: list[str] = []
        char_count = 0
        for line in all_lines[start - 1 : end]:
            if selected and char_count + len(line) + 1 > self.max_chars:
                break
            selected.append(line)
            char_count += len(line) + 1
        end = start + len(selected) - 1

        result = DocumentRange(
            start_line=start,
            end_line=end,
            total_lines=total,
            content="\n".join(selected),
            content_hash=file_hash,
        )
        annotated = annotate_lines(selected, start, self.max_line_chars)
        remaining = total - end
        suffix = (
            f"\n\n[{remaining} more lines — call again with offset={end + 1}]"
            if remaining > 0
            else ""
        )
        source = decoded.source_encoding
        encoding = f", decoded from {source}" if source else ""
        return ToolOutput(
            data=result,
            formatted=(
                f"lines {result.start_line}-{result.end_line} of "
                f"{result.total_lines} (hash {file_hash}{encoding}):"
                f"\n{annotated}{suffix}"
            ),
        )
