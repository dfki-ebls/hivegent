"""Grep tool callable — search documents for a pattern."""

import asyncio
import logging
from collections.abc import Iterator, Sequence
from contextlib import aclosing
from dataclasses import dataclass, replace
from pathlib import Path, PurePosixPath
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

logger = logging.getLogger(__name__)

_MAX_RESULTS = 10_000
"""Ceiling on ``max_results``, which bounds what a search holds in memory."""

_MAX_CONTEXT = 100
"""Ceiling on ``context``, since every block holds its context lines too."""

_FAILED = "Grep failed for pattern %r in %s"

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


def _globbed(local: str, glob: str | None) -> bool:
    """Whether a search under *glob* reads *local*, as ripgrep matches a glob.

    >>> _globbed("reports/q1.md", "*.md"), _globbed("q1.md", "reports/*.md")
    (True, False)
    """
    if glob is None:
        return True

    path = PurePosixPath(local)

    return (path if "/" in glob else PurePosixPath(path.name)).full_match(glob)


def _shadowed(sp: SearchPath, local: str, glob: str | None) -> bool:
    """Whether *local* is an original whose markdown description the search reads instead.

    Every entry keeps its markdown description beside the original it was
    projected from, so a text original (XML, CSV, source code) matches twice
    over with near-identical lines at shifted line numbers.  The description
    wins: it is the face of the entry the user edits and the chunker indexes,
    and it is regenerated whenever the original changes, so it never lags
    behind while the reverse does not hold.  It is decided per file, from the
    description being there to read, so a search streaming files in order
    need not wait for a later one, and a search globbed to the original's
    extension still reaches it, since then the description is not read.
    """
    if is_description_file(local):
        return False

    description = description_path_for_stem(stem_path_from_reference(local))

    return (
        _globbed(description, glob)
        and file_allowed(sp.filter_func, description)
        and (sp.path / description).is_file()
    )


async def _count_path(
    sp: SearchPath,
    pattern: str,
    glob: str | None,
    case_sensitive: bool,
    literal: bool,
    exclude_dirs: tuple[str, ...],
) -> tuple[list[_Hit], int]:
    """Count the matching lines of each file in one search path, and the files it hid.

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
    except Exception:
        logger.warning(_FAILED, pattern, sp.path, exc_info=True)
        return [], 0

    hits: list[_Hit] = []
    hidden = 0
    for local, count in counts.items():
        if not file_allowed(sp.filter_func, local) or _shadowed(sp, local, glob):
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


async def _search(
    paths: Sequence[SearchPath],
    pattern: str,
    glob: str | None,
    context: int,
    case_sensitive: bool,
    literal: bool,
    exclude_dirs: tuple[str, ...],
    limit: int,
) -> tuple[list[GrepMatch], int]:
    """The leading match blocks across *paths*, up to *limit* matching lines, and the files hidden.

    Each root streams its blocks in path order and the search stops reading
    at the limit, so a broad pattern costs what it shows, which leaves how
    many more there are unknown.  The hidden count is a floor for the same
    reason as :func:`_count_path`'s, and covers only what was read.
    """
    kept: list[GrepMatch] = []
    hidden: set[str] = set()
    shadowed: dict[str, bool] = {}
    left = limit

    for sp in paths:
        if not left:
            break

        if not sp.path.exists():
            continue

        found = rg_search(
            pattern,
            sp.path,
            glob=glob,
            context_lines=context,
            max_count=limit,
            case_sensitive=case_sensitive,
            literal=literal,
            exclude_dirs=exclude_dirs,
        )

        try:
            async with aclosing(found):
                async for rg_match in found:
                    local = rg_match.path

                    if local not in shadowed:
                        shadowed[local] = _shadowed(sp, local, glob)

                    if shadowed[local] or not file_allowed(sp.filter_func, local):
                        continue

                    if entry_ignored(local, exclude_dirs):
                        hidden.add(sp.prefixed(local))
                        continue

                    block, left = _cut(_grep_match(sp, rg_match), left)
                    kept.append(block)

                    if not left:
                        break

        # What the root yielded before it failed stays, as the rest of the search does.
        except Exception:
            logger.warning(_FAILED, pattern, sp.path, exc_info=True)

    return kept, len(hidden)


@dataclass(slots=True, frozen=True)
class GrepTool(AsyncPathTool[list[GrepMatch]]):
    """Search documents for a pattern.

    ``max_line_chars`` safeguards the LLM context against a single call
    flooding it with long wrapped markdown lines.  A matching line is clipped
    around its match, so the cut never takes the hit itself, and a context
    line from its start.  ``full_lines`` lifts the clip from both.

    Content mode streams the matches in path order and stops reading at
    ``max_results``, so it bounds what a search holds and reads rather than
    only what it renders, and the counting modes count every file in one run.
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
        hints: list[str] = []

        # The structured data is what backs the text, since the frontend
        # cites what it is given: the blocks shown in content mode, and in the
        # counting modes, which show no line, the files listed.
        if output_mode == "content":
            clip = LineClip(None if full_lines else self.max_line_chars)
            data, hidden = await _search(
                paths,
                pattern,
                glob,
                context,
                case_sensitive,
                literal,
                exclude,
                max_results,
            )
            body = self._format_matches(data, clip)
            shown = sum(line.is_match for block in data for line in block.lines)
            hints += clip.hints()
            hints += omission_hints(hidden=hidden, max_results=max_results, shown=shown)

            # The search stopped at the cap, so only a count says how far it reaches.
            if shown >= max_results:
                hints.append("output_mode='count' says how many lines match per file")
        else:
            results = await asyncio.gather(
                *(
                    _count_path(sp, pattern, glob, case_sensitive, literal, exclude)
                    for sp in paths
                )
            )
            hits = sorted(
                (hit for batch, _hidden in results for hit in batch),
                key=lambda hit: hit.filename,
            )
            listed = hits[:max_results]
            data = [GrepMatch(hit.filename, ()) for hit in listed]
            body = "\n".join(
                hit.filename
                if output_mode == "files_with_matches"
                else f"{hit.filename}: {hit.count}"
                for hit in listed
            )
            hints += omission_hints(
                hidden=sum(hidden for _batch, hidden in results),
                max_results=max_results,
                total=len(hits),
            )

        return ToolOutput(
            data=data, formatted=(body or "(no matches)") + hint_suffix(hints)
        )

    @staticmethod
    def _format_matches(matches: list[GrepMatch], clip: LineClip) -> str:
        """Render *matches* with each document path once as a heading.

        Every line then carries only its number with ``:`` for matches and
        ``-`` for context.  Distinct documents are split by :data:`BLOCK_SEP`
        and discontiguous blocks within a document by :data:`GROUP_SEP`.
        """
        return "".join(_iter_lines(matches, clip))


def _iter_lines(matches: list[GrepMatch], clip: LineClip) -> Iterator[str]:
    """Yield each match line with the separator that precedes it."""
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

            yield f"{prefix}{number_line(line.line_number, text, mark)}"
