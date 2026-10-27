"""Grep tool callable — search documents for a pattern."""

import asyncio
from collections import defaultdict
from collections.abc import Container, Sequence
from dataclasses import dataclass, replace
from itertools import groupby
from pathlib import Path
from typing import Annotated, Literal, override

from pydantic import Field

from ..converters import BINARY_SUFFIXES
from ..entries import (
    description_path_for_stem,
    is_description_file,
    stem_path_from_reference,
)
from ..subprocesses import RgMatch, rg_count, rg_search
from .base import (
    WORKSPACE_SCOPE_HINT,
    AsyncPathTool,
    FullLinesArg,
    IncludeIgnoredArg,
    SearchPath,
    ToolOutput,
    ToolRetry,
    entry_ignored,
    excluded_dirs,
    file_allowed,
    sidecar_hint,
)
from .formatting import (
    BLOCK_SEP,
    GROUP_SEP,
    LineClip,
    hint_suffix,
    number_line,
    omission_hints,
)

__all__ = [
    "GrepCaseSensitiveArg",
    "GrepContextArg",
    "GrepGlobArg",
    "GrepLine",
    "GrepLiteralArg",
    "GrepMatch",
    "GrepMaxResultsArg",
    "GrepOutputMode",
    "GrepOutputModeArg",
    "GrepPatternArg",
    "GrepTool",
]

_MAX_RESULTS = 10_000
"""Ceiling on ``max_results``, which bounds what a search holds in memory."""

_MAX_CONTEXT = 100
"""Ceiling on ``context``, since every block holds its context lines too."""

type GrepOutputMode = Literal["content", "files_with_matches", "count"]


@dataclass(slots=True, frozen=True)
class GrepLine:
    """A single line in a match block."""

    line_number: int
    text: str
    is_match: bool
    match_start: int | None = None
    """Character offset of the first match in *text*, ``None`` for context."""


@dataclass(slots=True, frozen=True)
class GrepMatch:
    """A match block in a document with a path relative to the search root."""

    filename: str
    lines: tuple[GrepLine, ...]


GrepPatternArg = Annotated[
    str,
    Field(
        description=(
            "Text or regular expression pattern to search for. Write it in the "
            "language the documents are likely written in, and retry in another "
            "language (e.g. English or the user's) when nothing matches."
        ),
    ),
]
GrepGlobArg = Annotated[
    str | None,
    Field(
        description=(
            f"Optional glob restricting which files are searched, matched "
            f"against workspace-relative paths (e.g. `*.md` or `reports/*.txt`). "
            f"{WORKSPACE_SCOPE_HINT}"
        ),
    ),
]
GrepContextArg = Annotated[
    int,
    Field(
        description="Number of context lines to include before and after a match.",
        ge=0,
        le=_MAX_CONTEXT,
    ),
]
GrepMaxResultsArg = Annotated[
    int,
    Field(
        description=(
            "Maximum number of matching lines to return, or of files in the "
            "`files_with_matches` and `count` modes."
        ),
        ge=1,
        le=_MAX_RESULTS,
    ),
]
GrepCaseSensitiveArg = Annotated[
    bool,
    Field(
        description=(
            "When true, match case exactly.  Defaults to case-insensitive search."
        ),
    ),
]
GrepLiteralArg = Annotated[
    bool,
    Field(
        description=(
            "When true, treat `pattern` as a literal string rather than a "
            "regular expression.  Use this to search for characters like "
            "`.`, `(`, or `?` without escaping them."
        ),
    ),
]
GrepOutputModeArg = Annotated[
    GrepOutputMode,
    Field(
        description=(
            "Output mode: `content` returns match blocks with context, "
            "`files_with_matches` returns only the filenames that contain "
            "a match, and `count` returns per-file match counts.  Use the "
            "lighter modes to scope a broad search before drilling into "
            "specific matches."
        ),
    ),
]


def _binary_glob(glob: str | None) -> str:
    """Refuse a search whose glob can only ever match what nothing reads.

    ripgrep skips a binary file without a word, so a search globbed to one
    answers "(no matches)" — a clean negative that is really a file never
    opened, and one a run reasonably takes for proof that the term is absent.
    The glob is the only place that intent is visible before the search, so
    that is where it is refused.

    The way out is :func:`sidecar_hint`'s and not a second copy of it: a glob
    is a path shape, so its ``<stem>.md`` is the glob that reaches the same
    entries' extracted text, and the tool that reads the original in place is
    named there once for every refusal in the codebase rather than here again.
    """
    if not glob or Path(glob).suffix.lower() not in BINARY_SUFFIXES:
        return ""

    return (
        f"'{glob}' matches only files whose bytes are not text, which a search "
        f"never opens, so it can only ever report no matches.{sidecar_hint(glob)}"
    )


@dataclass(slots=True, frozen=True)
class _Hit:
    """A file with matches, under the root that found it."""

    root: SearchPath
    local: str
    count: int
    """How many of its lines match."""

    @property
    def filename(self) -> str:
        """The file as every result spells it."""
        return self.root.prefixed(self.local)


def _shadowed(local: str, matched: Container[str]) -> bool:
    """Whether *local* is an original whose markdown description *matched* as well.

    Every entry keeps its markdown description beside the original it was
    projected from, so a text original (XML, CSV, source code) matches twice
    over with near-identical lines at shifted line numbers.  The description
    wins: it is the face of the entry the user edits and the chunker indexes,
    and it is regenerated whenever the original changes, so it never lags
    behind while the reverse does not hold.  An entry with no description hit
    is left alone, and a search globbed to the original's extension still
    reaches it, since then no description is in the result set to displace it.

    >>> _shadowed("doc.xml", {"doc.xml", "doc.md"}), _shadowed("doc.xml", {"doc.xml"})
    (True, False)
    """
    return (
        not is_description_file(local)
        and description_path_for_stem(stem_path_from_reference(local)) in matched
    )


async def _hits(
    sp: SearchPath,
    pattern: str,
    glob: str | None,
    case_sensitive: bool,
    literal: bool,
    exclude_dirs: tuple[str, ...],
) -> tuple[list[_Hit], int]:
    """The files with matches in one search path, and how many of them it hid.

    One run per workspace, each anchored in its own root, so an unprefixed
    ``reports/*.txt`` names that subdirectory of every workspace the call
    spans, which is what a glob without a scope prefix promises, while a
    prefixed one has already narrowed the paths to the workspace it names.

    The hidden count covers what the post-filter drops, ``.assets`` contents
    above all, and not the build and vendor directories ripgrep never enters,
    so it is a floor: worth saying that ``include_ignored`` reveals more, not
    exactly how much.
    """
    if not sp.path.exists():
        return [], 0

    try:
        counts = await rg_count(
            pattern,
            sp.path,
            glob=glob,
            case_sensitive=case_sensitive,
            literal=literal,
            exclude_dirs=exclude_dirs,
        )

    # Mostly a pattern ripgrep cannot parse, which the model can correct.
    except ValueError as error:
        raise ToolRetry(str(error)) from error

    allowed = {
        local: count
        for local, count in counts.items()
        if file_allowed(sp.filter_func, local)
    }
    hits: list[_Hit] = []
    hidden = 0

    for local, count in allowed.items():
        if _shadowed(local, allowed):
            continue

        if entry_ignored(local, exclude_dirs):
            hidden += 1
            continue

        hits.append(_Hit(sp, local, count))

    return hits, hidden


def _grep_match(sp: SearchPath, rg_match: RgMatch) -> GrepMatch:
    # ripgrep runs inside the root, so what it reports is already local.
    return GrepMatch(
        filename=sp.prefixed(rg_match.path),
        lines=tuple(
            GrepLine(
                line_number=line.line_number,
                text=line.text,
                is_match=line.is_match,
                match_start=line.match_start,
            )
            for line in rg_match.lines
        ),
    )


def _cut(block: GrepMatch, left: int) -> tuple[GrepMatch, int]:
    """*block* up to *left* matching lines, cut after its last kept one, and what is left.

    >>> line = GrepLine(1, "x", is_match=True)
    >>> block, left = _cut(GrepMatch("a", (line, line)), 1)
    >>> len(block.lines), left
    (1, 0)
    """
    lines: list[GrepLine] = []

    for line in block.lines:
        if line.is_match:
            if not left:
                break

            left -= 1

        lines.append(line)

    return replace(block, lines=tuple(lines)), left


async def _blocks(
    hits: Sequence[_Hit],
    pattern: str,
    context: int,
    case_sensitive: bool,
    literal: bool,
    limit: int,
) -> list[GrepMatch]:
    """The match blocks of the leading *hits*, up to *limit* matching lines.

    The counts say which files the limit reaches, so only those are read for
    their lines, and the last one is cut after its last line shown.
    """
    leading: list[_Hit] = []
    reached = 0

    for hit in hits:
        if reached >= limit:
            break

        leading.append(hit)
        reached += hit.count

    groups = [
        (sp, [hit.local for hit in group])
        for sp, group in groupby(leading, key=lambda hit: hit.root)
    ]
    found = await asyncio.gather(
        *(
            rg_search(
                pattern,
                sp.path,
                files,
                context_lines=context,
                max_count=limit,
                case_sensitive=case_sensitive,
                literal=literal,
            )
            for sp, files in groups
        )
    )
    blocks: defaultdict[str, list[GrepMatch]] = defaultdict(list)

    for (sp, _files), rg_matches in zip(groups, found, strict=True):
        for rg_match in rg_matches:
            block = _grep_match(sp, rg_match)
            blocks[block.filename].append(block)

    kept: list[GrepMatch] = []
    left = limit

    for hit in leading:
        for block in blocks[hit.filename]:
            if left:
                block, left = _cut(block, left)
                kept.append(block)

    return kept


@dataclass(slots=True, frozen=True)
class GrepTool(AsyncPathTool[list[GrepMatch]]):
    """Search documents for a pattern.

    ``max_line_chars`` safeguards the LLM context against a single call
    flooding it with long wrapped markdown lines.  A matching line is clipped
    around its match, so the cut never takes the hit itself, and a context
    line from its start.  ``full_lines`` lifts the clip from both.

    Every mode counts the matches of every file in one parallel run per root,
    and content mode then reads the lines of only the files it shows, in path
    order up to ``max_results``, so what a search holds is bounded by what it
    renders while its hints still know the total.
    """

    max_line_chars: int = 200

    @override
    async def __call__(
        self,
        pattern: GrepPatternArg,
        glob: GrepGlobArg = None,
        context: GrepContextArg = 2,
        max_results: GrepMaxResultsArg = 50,
        case_sensitive: GrepCaseSensitiveArg = False,
        literal: GrepLiteralArg = False,
        output_mode: GrepOutputModeArg = "content",
        include_ignored: IncludeIgnoredArg = False,
        full_lines: FullLinesArg = False,
    ) -> ToolOutput[list[GrepMatch]]:
        """Search documents for a pattern.

        Suited to short exact terms, names, or codes and to structural matches
        through patterns, while search finds content by phrase across large
        collections and comes first.
        Defaults to case-insensitive regex search.  Set ``literal=True``
        to disable regex interpretation, or ``case_sensitive=True`` for
        exact case matching.  Use ``output_mode`` to switch between full
        match context, filenames only, or per-file counts.

        A document that matches both in its markdown description and in the
        original file it was projected from is reported once, under the
        description.
        """
        if refusal := _binary_glob(glob):
            raise ToolRetry(refusal)

        exclude = excluded_dirs(include_ignored)
        # A scope prefix on the glob narrows the search to one workspace, and
        # `scoped` folds it to NFC to match the canonically named files.
        # `pattern` deliberately stays raw: it matches file content, which is
        # never normalized, so folding it would stop it matching decomposed text.
        paths, glob = self.scoped(glob)
        results = await asyncio.gather(
            *(
                _hits(sp, pattern, glob, case_sensitive, literal, exclude)
                for sp in paths
            )
        )
        hits = sorted(
            (hit for batch, _hidden in results for hit in batch),
            key=lambda hit: hit.filename,
        )
        hints: list[str] = []

        # The structured data is what backs the text, since the frontend
        # cites what it is given: the blocks shown in content mode, and in the
        # counting modes, which show no line, the files listed.
        if output_mode == "content":
            clip = LineClip(None if full_lines else self.max_line_chars)
            data = await _blocks(
                hits, pattern, context, case_sensitive, literal, max_results
            )
            body = _format_matches(data, clip)
            total = sum(hit.count for hit in hits)
            hints += clip.hints()
        else:
            listed = hits[:max_results]
            data = [GrepMatch(hit.filename, ()) for hit in listed]
            body = "\n".join(
                hit.filename
                if output_mode == "files_with_matches"
                else f"{hit.filename}: {hit.count}"
                for hit in listed
            )
            total = len(hits)

        hints += omission_hints(
            hidden=sum(hidden for _batch, hidden in results),
            max_results=max_results,
            total=total,
        )

        return ToolOutput(
            data=data, formatted=(body or "(no matches)") + hint_suffix(hints)
        )


def _format_matches(matches: Sequence[GrepMatch], clip: LineClip) -> str:
    """Render *matches* with each document path once as a heading.

    Every line then carries only its number with ``:`` for matches and
    ``-`` for context.  Distinct documents are split by :data:`BLOCK_SEP`
    and discontiguous blocks within a document by :data:`GROUP_SEP`.
    """
    parts: list[str] = []
    prev_file: str | None = None

    for m in matches:
        if prev_file is None:
            head = f"{m.filename}\n"
        elif m.filename != prev_file:
            head = f"{BLOCK_SEP}{m.filename}\n"
        else:
            head = GROUP_SEP

        prev_file = m.filename

        for i, line in enumerate(m.lines):
            prefix = head if i == 0 else "\n"
            mark = ":" if line.is_match else "-"

            # A match is clipped around its hit, so the cut never takes it.
            text = (
                clip.around(line.text, line.match_start or 0)
                if line.is_match
                else clip(line.text)
            )
            parts.append(f"{prefix}{number_line(line.line_number, text, mark)}")

    return "".join(parts)
