"""Document listing and reading tool callables."""

import asyncio
import logging
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from itertools import chain, islice
from os import stat_result
from pathlib import Path, PurePosixPath
from stat import S_ISDIR, S_ISREG
from typing import Annotated, override

from pydantic import ConfigDict, Field

from ..config import content_hash, normalize_unicode
from ..converters import vision_media_type
from .base import (
    WORKSPACE_SCOPE_HINT,
    AsyncPathTool,
    Batch,
    BatchShare,
    FullLinesArg,
    IncludeIgnoredArg,
    SearchPath,
    ToolOutput,
    ToolRetry,
    batch_field,
    entry_ignored,
    entry_stat,
    excluded_dirs,
    file_allowed,
    missing_directory_retry,
    query_hint,
    read_text_or_retry,
    resolve_directory,
    resolve_file_or_retry,
    run_batch,
    scope_paths,
    sidecar_hint,
)
from .formatting import (
    LineClip,
    fit_lines,
    hint_suffix,
    iter_annotated,
    omission_hints,
)

__all__ = [
    "DocumentFilePathArg",
    "DocumentLimitArg",
    "DocumentMaxDepthArg",
    "DocumentMaxResultsArg",
    "DocumentOffsetArg",
    "DocumentPathArg",
    "DocumentPatternsArg",
    "DocumentRange",
    "DocumentRead",
    "DocumentReadsArg",
    "DocumentSummary",
    "ListDocumentsTool",
    "ReadDocumentTool",
]

logger = logging.getLogger(__name__)

_READ_CONCURRENCY = 4
"""Documents decoded at once by one batched read, each on its own thread."""

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

    file_path: str
    start_line: int
    end_line: int
    total_lines: int
    content: str
    content_hash: str
    """Fingerprint of the *full* document, for ``expected_hash`` on a later edit."""


DocumentFilePathArg = Annotated[
    str,
    Field(description="Full workspace path of the document to operate on."),
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
DocumentMaxDepthArg = Annotated[
    int | None,
    Field(
        description="Maximum nesting depth relative to the selected directory.",
        ge=1,
    ),
]
_MAX_LISTED_ENTRIES = 10_000
"""Ceiling on ``max_results``, which bounds what a listing holds in memory."""

DocumentMaxResultsArg = Annotated[
    int,
    Field(
        description="Maximum number of entries to return.",
        ge=1,
        le=_MAX_LISTED_ENTRIES,
    ),
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
            "Maximum number of lines to return. When omitted, reads as many as "
            "the character budget allows."
        ),
        ge=1,
    ),
]


@dataclass(slots=True, frozen=True)
class DocumentRead:
    """One document to read, and the window of lines to read from it."""

    __pydantic_config__ = ConfigDict(extra="forbid")

    file_path: DocumentFilePathArg
    offset: DocumentOffsetArg = 1
    limit: DocumentLimitArg = None

    @property
    def key(self) -> str:
        """The path, plus the window when it is not the default one.

        >>> DocumentRead("~/a.md").key
        '~/a.md'
        >>> DocumentRead("~/a.md", offset=40, limit=10).key
        '~/a.md (offset=40, limit=10)'
        """
        window = [f"offset={self.offset}"] if self.offset != 1 else []
        window += [f"limit={self.limit}"] if self.limit is not None else []

        return f"{self.file_path} ({', '.join(window)})" if window else self.file_path


DocumentReadsArg = Annotated[
    list[DocumentRead],
    batch_field(
        "Documents to read, each with an optional window. Read every document "
        "you need in one call rather than one call each, since the output budget is "
        "shared between them.",
        max_items=20,
    ),
]

DocumentPatternsArg = Annotated[
    list[str] | None,
    batch_field(
        "Optional glob patterns matched against workspace-relative filenames "
        "at any depth (e.g. `*.md`, `reports/*.csv`), so only files matching "
        "any of them are listed, each once, and `max_depth` does not apply. "
        "To restrict to one workspace, prefix the `path` argument rather than "
        "the patterns."
    ),
]


def _relative_depth(filepath: str, subdir: str | None) -> int:
    """Nesting depth of *filepath* below *subdir*, a direct child counting as 1.

    Args:
        filepath: Document path relative to the search root.
        subdir: Canonical directory the walk started at, or ``None`` for the
            root itself.  Every walked entry lies under it, since the walk is
            rooted there rather than filtered down to it.
    """
    relative = filepath[len(subdir) + 1 :] if subdir else filepath

    return relative.count("/") + 1


def _normalize_subdir(subdir: str | None) -> str | None:
    """Fold the harmless spellings of a directory-filter argument.

    ``./docs/``, ``docs/``, and ``docs`` name one directory, and ``.`` names
    the root, which is the scope of omitting the argument entirely.  A ``..``
    segment is left to :func:`canonical_local_path`, which can resolve it
    against the root it is relative to.
    """
    if not subdir:
        return None
    normalized = PurePosixPath(subdir).as_posix()

    return None if normalized == "." else normalized


def _scoped_directory(
    paths: tuple[SearchPath, ...], path: str | None
) -> tuple[tuple[SearchPath, ...], tuple[tuple[SearchPath, Path], ...], str | None]:
    """Resolve a directory argument into the roots a walk starts from.

    Returns the search paths *path*'s scope prefix narrows to, one
    ``(search_path, root)`` pair per root that holds the directory, and the
    directory folded to the canonical spelling its entries are named under.
    Resolving once here rather than inside the walk means a directory costs
    one fold and one stat for the whole call, shared by a scan and by the
    second unfiltered scan its hidden-entry hint costs, and it bounds the walk
    to the subtree instead of sweeping the workspace and discarding the rest.

    Raises:
        ToolRetry: when *path* names a directory no accessible root holds.
    """
    scoped, subdir = scope_paths(paths, path)
    subdir = _normalize_subdir(subdir)
    if subdir is None:
        return scoped, tuple((sp, sp.path) for sp in scoped if sp.path.is_dir()), None

    roots, canonical = resolve_directory(scoped, subdir)
    if not roots:
        raise missing_directory_retry(scoped, path or subdir)

    return scoped, roots, canonical


def _empty_message(noun: str, paths: tuple[SearchPath, ...], subdir: str | None) -> str:
    """Render an empty result as what the call covered.

    A bare ``(no documents)`` reads as an empty workspace whatever the call
    actually covered, so the scope is named in the grammar a path argument
    takes back, never as a host path.  A single unscoped root has no prefix to
    give, which is the one case with no path to name.
    """
    if len(paths) == 1:
        label = paths[0].prefixed(subdir or "")
        location = f" under {label!r}" if label else " in the workspace"
    elif subdir is not None:
        location = f" under {subdir!r} in any accessible workspace"
    else:
        location = " in accessible workspaces"

    return f"({noun}{location})"


def _walk_entries(
    roots: tuple[tuple[SearchPath, Path], ...],
    pattern: str | None,
    *,
    include_dirs: bool,
) -> Iterator[tuple[SearchPath, str, stat_result]]:
    """Walk pre-resolved roots yielding ``(sp, relative, stat)`` tuples.

    Applies ``pattern`` with rglob semantics and the search path's own
    ``filter_func``.  Callers handle capping and any additional filters
    (depth and the default exclusions, which they count rather than drop
    unseen).

    Roots arrive folded and containment-checked from :func:`_scoped_directory`,
    so a directory argument is resolved once for the whole call rather than
    once per pass over it.  Every entry is still named relative to its search
    path rather than to the root the walk started at, since that is the name a
    filter, a prefix, and a caller all address it by.  A symlink is skipped
    rather than listed: its name is an alias for a file the filter was never
    asked about, which is also why the upload pipeline refuses one.

    The one ``lstat`` each entry costs is handed to the caller rather than
    thrown away, since the size and mtime a listing reports come off exactly
    that stat and nothing about the entry can have moved in between.
    """
    for sp, root in roots:
        for absolute in sorted(root.rglob(pattern or "*")):
            st = entry_stat(absolute)
            if st is None:
                continue
            is_dir = S_ISDIR(st.st_mode)
            if is_dir and not include_dirs:
                continue
            if not is_dir and not S_ISREG(st.st_mode):
                continue
            rel = str(absolute.relative_to(sp.path).as_posix())
            if not file_allowed(sp.filter_func, rel):
                continue
            yield sp, rel, st


def _scan_entries(
    roots: tuple[tuple[SearchPath, Path], ...],
    patterns: list[str] | None,
    subdir: str | None,
    max_depth: int | None,
    max_results: int,
    exclude_dirs: tuple[str, ...],
) -> tuple[list[DocumentSummary], list[str]]:
    """Collect entries, with hints naming what was left out.

    Without *patterns* this lists files and directories down to *max_depth*.
    With them it lists the files any pattern matches at any depth.  Each
    pattern is a path argument matched against canonically named entries, so
    it is folded here: unlike a scoped subdirectory it reaches neither
    :func:`resolve_search_path` nor :func:`scope_paths`.  One walk per pattern
    keeps each pattern's own rglob semantics, and a file two of them match is
    listed once.
    """
    if patterns:
        unique = dict.fromkeys(map(normalize_unicode, patterns))
        walk = chain.from_iterable(
            _walk_entries(roots, pattern, include_dirs=False) for pattern in unique
        )
        max_depth = None
    else:
        walk = _walk_entries(roots, None, include_dirs=True)

    results: dict[str, DocumentSummary] = {}
    hidden: set[str] = set()
    deeper = 0

    for sp, rel, st in walk:
        filename = sp.prefixed(rel)
        ignored = entry_ignored(rel, exclude_dirs)

        if max_depth is not None and _relative_depth(rel, subdir) > max_depth:
            deeper += not ignored
            continue

        if ignored:
            hidden.add(filename)
            continue

        is_dir = S_ISDIR(st.st_mode)
        results[filename] = DocumentSummary(
            filename=filename,
            size=0 if is_dir else st.st_size,
            modified_at=datetime.fromtimestamp(st.st_mtime, tz=UTC),
            is_directory=is_dir,
        )

        if len(results) >= max_results:
            break

    return list(results.values()), omission_hints(
        hidden=len(hidden),
        deeper=deeper,
        max_depth=max_depth,
        max_results=max_results,
        shown=len(results),
    )


@dataclass(slots=True, frozen=True)
class ListDocumentsTool(AsyncPathTool[list[DocumentSummary]]):
    """List available documents with sizes and dates, optionally by pattern."""

    @override
    async def __call__(
        self,
        path: DocumentPathArg = None,
        patterns: DocumentPatternsArg = None,
        max_depth: DocumentMaxDepthArg = 1,
        max_results: DocumentMaxResultsArg = 200,
        include_ignored: IncludeIgnoredArg = False,
    ) -> ToolOutput[list[DocumentSummary]]:
        """List available documents with sizes and dates.

        Pass ``patterns`` to list only the files whose names match, at any
        depth.  Common build and vendor directories and the contents of
        ``.assets`` payload directories are skipped by default, pass
        ``include_ignored=True`` to include them.
        """
        # The walk stats every entry, so it goes to a thread rather than the
        # event loop, which is where pydantic-ai ran it while it was sync.
        return await asyncio.to_thread(
            self._list, path, patterns, max_depth, max_results, include_ignored
        )

    def _list(
        self,
        path: str | None,
        patterns: list[str] | None,
        max_depth: int | None,
        max_results: int,
        include_ignored: bool,
    ) -> ToolOutput[list[DocumentSummary]]:
        """Scan the workspace and render one line per entry."""
        paths, roots, subdir = _scoped_directory(self.resolved_paths, path)
        entries, hints = _scan_entries(
            roots,
            patterns,
            subdir,
            max_depth,
            max_results,
            excluded_dirs(include_ignored),
        )
        note = hint_suffix(hints)

        if not entries:
            noun = "no matches" if patterns else "no documents"

            return ToolOutput(
                data=entries, formatted=_empty_message(noun, paths, subdir) + note
            )

        lines: list[str] = []

        for d in entries:
            date = d.modified_at.strftime("%Y-%m-%d %H:%M") if d.modified_at else "-"
            kind = "d" if d.is_directory else "-"
            lines.append(f"{kind} {date}  {_humanize_size(d.size):>6}  {d.filename}")

        return ToolOutput(data=entries, formatted="\n".join(lines) + note)


@dataclass(slots=True, frozen=True)
class ReadDocumentTool(AsyncPathTool[Batch[DocumentRange]]):
    """Read documents' content as line ranges with line numbers.

    Two budgets sit on different axes.  ``max_chars`` bounds the window of
    content selected, and ``max_line_chars`` clips each numbered line so a
    single very long line (a base64 embedded image, a minified bundle, a wide
    markdown table row) cannot flood the model context.  An agent sets
    ``max_chars`` so that a window rendered with its line numbers stays within
    what a tool return shows whole, since a read is resumed from an offset
    rather than from a saved copy of the window.  ``full_lines`` opts out of
    the per-line clip for content whose tail carries meaning.  The structured
    ``content`` keeps the lines the model was shown, untruncated, for the
    frontend.  A call reading several documents splits ``max_chars`` between
    them.
    """

    max_chars: int = 40_000
    max_line_chars: int = 2000

    @override
    async def __call__(
        self,
        reads: DocumentReadsArg,
        full_lines: FullLinesArg = False,
    ) -> ToolOutput[Batch[DocumentRange]]:
        """Read the content of one or more documents.

        Each read returns the lines from ``offset`` (1-indexed) up to
        ``limit`` lines, each prefixed with its line number, under a header
        naming the document.  The lines are also clamped by a per-call
        character budget shared by the reads, and the output reports how many
        lines remain so the caller can issue a follow-up with a higher
        ``offset``.  Long
        lines are clipped unless ``full_lines`` is set, and the output says
        when that happened so the caller can ask for them whole.  A file
        stored in a legacy encoding is decoded transparently, with the
        source encoding named next to the hash.  A read that fails is
        reported in place without failing the others.
        """

        async def read(
            item: DocumentRead, share: BatchShare
        ) -> ToolOutput[DocumentRange]:
            return await asyncio.to_thread(self._read, item, full_lines, share)

        return await run_batch(
            reads, read, key=lambda item: item.key, concurrency=_READ_CONCURRENCY
        )

    def _read(
        self,
        read: DocumentRead,
        full_lines: bool,
        share: BatchShare,
    ) -> ToolOutput[DocumentRange]:
        """Decode the file and render the requested window of lines."""
        offset, limit = read.offset, read.limit
        sp, local, absolute = resolve_file_or_retry(self.resolved_paths, read.file_path)
        # Reported as the document is named, which a client keys its coverage by.
        file_path = sp.prefixed(local)

        # Reads are uniform: the requested file is read as text and never
        # silently swapped for another.  Non-markdown inputs are pointed elsewhere
        # only through the error message — a vision-capable binary (image, PDF, video)
        # goes to read_binary_document (some models ingest those natively, and
        # PDFs get custom page rendering), while any non-markdown original's
        # extracted text stays reachable by requesting its ``<stem>.md`` sidecar,
        # whose read then re-runs the same containment checks.
        hint = sidecar_hint(file_path)

        # The same table the write gateway consults, so the two tools cannot end
        # up disagreeing about which files are text.
        media_type = vision_media_type(file_path)
        if media_type is not None:
            raise ToolRetry(
                f"'{file_path}' is a {media_type} binary, use read_binary_document "
                f"to send it to a vision model.{hint}"
            )

        # Legacy encodings are decoded rather than refused, and the encoding is
        # reported below: the same seam the upload pipeline and the editing
        # tools use, so a hash taken here still matches on a later edit.
        decoded = read_text_or_retry(absolute, file_path, hint)
        file_hash = content_hash(decoded.text)
        all_lines = decoded.text.splitlines()
        total = len(all_lines)
        start = max(1, offset)
        if total == 0:
            empty = DocumentRange(
                file_path=file_path,
                start_line=start,
                end_line=start - 1,
                total_lines=0,
                content="",
                content_hash=file_hash,
            )
            return ToolOutput(data=empty, formatted="(empty file)")
        if start > total:
            past_eof = DocumentRange(
                file_path=file_path,
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

        stop = total if limit is None else min(total, start + limit - 1)

        # Cap by character budget: one giant line still gets returned alone so
        # the caller sees something rather than an empty range.  The lines are
        # measured as rendered, numbered and clipped, since the numbers of many
        # short lines would otherwise push the window past the spill threshold.
        # The measuring clip is a throwaway, as it also sees the line that
        # did not fit.
        cap = None if full_lines else self.max_line_chars
        window = iter_annotated(
            islice(all_lines, start - 1, stop), start, LineClip(cap)
        )
        kept = fit_lines(window, share.of(self.max_chars))
        selected = all_lines[start - 1 : start - 1 + kept]
        clip = LineClip(cap)
        body = "\n".join(iter_annotated(selected, start, clip))
        end = start + len(selected) - 1

        result = DocumentRange(
            file_path=file_path,
            start_line=start,
            end_line=end,
            total_lines=total,
            content="\n".join(selected),
            content_hash=file_hash,
        )

        # A clipped line is the one truncation the reader cannot detect from
        # the output alone, so it is named: a wide table's trailing columns
        # are gone with nothing but an ellipsis to show for it.
        hints: list[str] = []

        # Reading a table one line at a time is what query_table exists to
        # avoid, and this is the only place the caller finds that out at the
        # moment it matters -- a JSON document read line by line spends the
        # context on records the question never asked about the same way.  The
        # entry decides, not the path in hand, since the table this read was
        # served is the projection of the file those tools take.
        if query := query_hint(sp, local):
            hints.append(query)

        hints += clip.hints()

        remaining = total - end

        if remaining > 0:
            hints.append(f"{remaining} more lines, call again with offset={end + 1}")

        suffix = hint_suffix(hints)
        source = decoded.source_encoding
        encoding = f", decoded from {source}" if source else ""
        return ToolOutput(
            data=result,
            formatted=(
                f"lines {result.start_line}-{result.end_line} of "
                f"{result.total_lines} (hash {file_hash}{encoding}):"
                f"\n{body}{suffix}"
            ),
        )
