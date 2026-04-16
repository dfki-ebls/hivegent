"""Grep tool callable — search documents for a pattern."""

import asyncio
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, override

from pydantic import Field

from ..subprocesses import rg_search
from .base import AsyncPathTool, SearchPath, ToolOutput, file_allowed

__all__ = [
    "ContextLinesArg",
    "GrepLine",
    "GrepMatch",
    "GrepMaxResultsArg",
    "GrepPathArg",
    "GrepPatternArg",
    "GrepTool",
]

logger = logging.getLogger(__name__)

_BLOCK_SEP = "\n--\n"


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
GrepPathArg = Annotated[
    str | None,
    Field(
        description=(
            "Optional filename or glob pattern to restrict which files "
            "are searched (e.g. `report.md` or `*.md`)."
        ),
    ),
]
ContextLinesArg = Annotated[
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


async def _search_path(
    sp: SearchPath,
    pattern: str,
    path: str | None,
    context_lines: int,
) -> list[GrepMatch]:
    """Run ripgrep against a single search path."""
    if not sp.path.exists():
        return []
    matches: list[GrepMatch] = []
    try:
        for rg_match in await rg_search(
            pattern,
            sp.path,
            glob=path,
            context_lines=context_lines,
            case_sensitive=False,
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
        path: GrepPathArg = None,
        context_lines: ContextLinesArg = 2,
        max_results: GrepMaxResultsArg = 50,
    ) -> ToolOutput[list[GrepMatch]]:
        """Search documents for a pattern (case-insensitive)."""
        results = await asyncio.gather(
            *(
                _search_path(sp, pattern, path, context_lines)
                for sp in self.resolved_paths
            )
        )
        all_matches = [m for batch in results for m in batch]
        all_matches = all_matches[:max_results]
        if not all_matches:
            return ToolOutput(data=all_matches, formatted="(no matches)")
        return ToolOutput(data=all_matches, formatted=self._format_matches(all_matches))

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
                f"refine your pattern, narrow the path, or lower context_lines)"
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
