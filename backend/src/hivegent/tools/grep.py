"""Grep tool callable — search documents for a pattern."""

import asyncio
import logging
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Literal, override

from pydantic import Field

from ..entries import is_description_file, stem_path_from_reference
from ..subprocesses import rg_search
from .base import (
    WORKSPACE_SCOPE_HINT,
    AsyncPathTool,
    FullLinesArg,
    IncludeIgnoredArg,
    SearchPath,
    ToolOutput,
    entry_visible,
    excluded_dirs,
)
from .formatting import BLOCK_SEP, GROUP_SEP, cap_lines, number_line, truncate_line

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

type GrepOutputMode = Literal["content", "files_with_matches", "count"]


@dataclass(slots=True, frozen=True)
class GrepLine:
    """A single line in a match block."""

    line_number: int
    text: str
    is_match: bool


@dataclass(slots=True, frozen=True)
class GrepMatch:
    """A match block in a document with a path relative to the search root."""

    filename: str
    lines: tuple[GrepLine, ...]


GrepPatternArg = Annotated[
    str,
    Field(description="Text or regular expression pattern to search for."),
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
    ),
]
GrepMaxResultsArg = Annotated[
    int,
    Field(
        description="Maximum total number of matches to return.",
        ge=1,
        le=1000,
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


def _local_name(sp: SearchPath, path: str) -> str | None:
    """Return *path* relative to the search root, or ``None`` if it escaped it."""
    try:
        return Path(path).relative_to(sp.path).as_posix()
    except ValueError:
        return None


def _drop_shadowed_originals(matches: list[GrepMatch]) -> list[GrepMatch]:
    """Drop original hits whose own markdown description matched as well.

    Every entry keeps its markdown description beside the original it was
    projected from, so a text original (XML, CSV, source code) matches twice
    over with near-identical lines at shifted line numbers.  The description
    wins: it is the face of the entry the user edits and the chunker indexes,
    and it is regenerated whenever the original changes, so it never lags
    behind while the reverse does not hold.  An entry with no description hit
    is left alone, and a search explicitly globbed to the original's
    extension still reaches it, since then no description is in the result
    set to displace it.
    """
    classified = [
        (m, is_description_file(m.filename), stem_path_from_reference(m.filename))
        for m in matches
    ]
    described = {stem for _, is_description, stem in classified if is_description}
    return [
        m
        for m, is_description, stem in classified
        if is_description or stem not in described
    ]


async def _search_path(
    sp: SearchPath,
    pattern: str,
    glob: str | None,
    context: int,
    case_sensitive: bool,
    literal: bool,
    exclude_dirs: tuple[str, ...],
) -> list[GrepMatch]:
    """Run ripgrep against a single search path."""
    if not sp.path.exists():
        return []
    try:
        rg_matches = await rg_search(
            pattern,
            sp.path,
            glob=glob,
            context_lines=context,
            case_sensitive=case_sensitive,
            literal=literal,
            exclude_dirs=exclude_dirs,
        )
    except Exception:
        logger.warning(
            "Grep failed for pattern %r in %s", pattern, sp.path, exc_info=True
        )
        return []

    matches: list[GrepMatch] = []
    for rg_match in rg_matches:
        filename = _local_name(sp, rg_match.path)
        if filename is None or not entry_visible(sp, filename, exclude_dirs):
            continue
        matches.append(
            GrepMatch(
                filename=sp.prefixed(filename),
                lines=tuple(
                    GrepLine(
                        line_number=line.line_number,
                        text=line.text,
                        is_match=line.is_match,
                    )
                    for line in rg_match.lines
                ),
            )
        )
    return matches


@dataclass(slots=True, frozen=True)
class GrepTool(AsyncPathTool[list[GrepMatch]]):
    """Search documents for a pattern.

    ``max_line_chars`` and ``max_formatted_chars`` safeguard the LLM
    context against a single call flooding it with long wrapped
    markdown lines or too many merged blocks.  The caller can always
    issue a more specific follow-up to get more detail.
    """

    max_line_chars: int = 200
    max_formatted_chars: int = 10_000

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
        # Context is wasted work when the formatted output discards it.
        effective_context = context if output_mode == "content" else 0
        exclude = excluded_dirs(include_ignored)
        # A scope prefix on the glob narrows the search to one workspace, and
        # `scoped` folds it to NFC to match the canonically named files.
        # `pattern` deliberately stays raw: it matches file content, which is
        # never normalized, so folding it would stop it matching decomposed text.
        paths, glob = self.scoped(glob)
        results = await asyncio.gather(
            *(
                _search_path(
                    sp,
                    pattern,
                    glob,
                    effective_context,
                    case_sensitive,
                    literal,
                    exclude,
                )
                for sp in paths
            )
        )
        all_matches = _drop_shadowed_originals([m for batch in results for m in batch])
        if not all_matches:
            return ToolOutput(data=all_matches, formatted="(no matches)")
        if output_mode == "files_with_matches":
            filenames = sorted({m.filename for m in all_matches})[:max_results]
            return ToolOutput(data=all_matches, formatted="\n".join(filenames))
        if output_mode == "count":
            counts: dict[str, int] = {}
            for m in all_matches:
                counts[m.filename] = counts.get(m.filename, 0) + sum(
                    1 for line in m.lines if line.is_match
                )
            lines = [f"{fn}: {n}" for fn, n in sorted(counts.items())[:max_results]]
            return ToolOutput(data=all_matches, formatted="\n".join(lines))
        capped = all_matches[:max_results]
        return ToolOutput(
            data=capped, formatted=self._format_matches(capped, full_lines)
        )

    def _format_matches(self, matches: list[GrepMatch], full_lines: bool = False) -> str:
        # Budget at the line level rather than the block level: ripgrep can
        # merge many nearby hits into one block whose formatted form dwarfs
        # the budget, and dropping it whole would print nothing but a notice.
        total_lines = sum(len(m.lines) for m in matches)
        line_cap = None if full_lines else self.max_line_chars
        body, omitted = cap_lines(
            self._iter_lines(matches, line_cap), self.max_formatted_chars, ""
        )
        if not omitted:
            return body
        notice = (
            f"({omitted} of {total_lines} lines omitted, "
            f"reduce context or narrow the pattern/glob)"
        )
        return f"{body}{BLOCK_SEP}{notice}"

    def _iter_lines(
        self, matches: list[GrepMatch], max_line_chars: int | None
    ) -> Iterator[str]:
        """Yield each match line with the separator that precedes it.

        The document path is emitted once as a heading; every line then
        carries only its number with ``:`` for matches and ``-`` for context.
        Distinct documents are split by :data:`BLOCK_SEP` and discontiguous
        blocks within a document by :data:`GROUP_SEP`, with each heading folded
        into its block's first line so the two stay together under the budget.
        """
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
                body = number_line(
                    line.line_number,
                    truncate_line(line.text, max_line_chars),
                    mark,
                )
                yield f"{prefix}{body}"
