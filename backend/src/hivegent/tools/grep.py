"""Grep tool callable — search documents for a pattern."""

import asyncio
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Literal, override

from pydantic import Field

from ..subprocesses import rg_search
from .base import (
    AsyncPathTool,
    IncludeIgnoredArg,
    SearchPath,
    ToolOutput,
    excluded_dirs,
    file_allowed,
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

_BLOCK_SEP = "\n--\n"

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
            "Optional glob pattern restricting which files are searched "
            "(e.g. `*.md` or `reports/*.txt`)."
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
    matches: list[GrepMatch] = []
    try:
        for rg_match in await rg_search(
            pattern,
            sp.path,
            glob=glob,
            context_lines=context,
            case_sensitive=case_sensitive,
            literal=literal,
            exclude_dirs=exclude_dirs,
        ):
            filename = str(Path(rg_match.path).relative_to(sp.path))
            if file_allowed(sp.filter_func, filename):
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
    except Exception:
        logger.warning("Grep failed for pattern %r in %s", pattern, sp.path)
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
    ) -> ToolOutput[list[GrepMatch]]:
        """Search documents for a pattern.

        Defaults to case-insensitive regex search.  Set ``literal=True``
        to disable regex interpretation, or ``case_sensitive=True`` for
        exact case matching.  Use ``output_mode`` to switch between full
        match context, filenames only, or per-file counts.
        """
        # Context is wasted work when the formatted output discards it.
        effective_context = context if output_mode == "content" else 0
        exclude = excluded_dirs(include_ignored)
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
                for sp in self.resolved_paths
            )
        )
        all_matches = [m for batch in results for m in batch]
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
        return ToolOutput(data=capped, formatted=self._format_matches(capped))

    def _format_matches(self, matches: list[GrepMatch]) -> str:
        parts: list[str] = []
        total = 0
        shown = 0
        for m in matches:
            block = self._format_match(m)
            added = len(block) + (len(_BLOCK_SEP) if parts else 0)
            if total + added > self.max_formatted_chars:
                break
            if parts:
                parts.append(_BLOCK_SEP)
            parts.append(block)
            total += added
            shown += 1
        omitted = len(matches) - shown
        if omitted:
            parts.append(
                f"{_BLOCK_SEP}({omitted} more matches omitted — "
                f"refine your pattern, narrow the glob, or lower context)"
            )
        return "".join(parts)

    def _format_match(self, m: GrepMatch) -> str:
        return "\n".join(
            f"{m.filename}:{line.line_number}"
            f"{':' if line.is_match else '-'}{self._truncate_line(line.text)}"
            for line in m.lines
        )

    def _truncate_line(self, text: str) -> str:
        if len(text) <= self.max_line_chars:
            return text
        return text[: self.max_line_chars - 1] + "…"
