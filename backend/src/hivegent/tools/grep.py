"""Grep tool callable — search documents for a pattern."""

import asyncio
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, override

from pydantic import Field

from ..subprocesses import rg_search
from .base import PathsTool, SearchPath, ToolOutput, file_allowed

__all__ = [
    "CaseSensitiveArg",
    "ContextLinesArg",
    "GrepMatch",
    "GrepMaxResultsArg",
    "GrepPathArg",
    "GrepPatternArg",
    "GrepTool",
]

logger = logging.getLogger(__name__)


@dataclass(slots=True, frozen=True)
class GrepMatch:
    """A pattern match in a document with a path relative to the search root."""

    filename: str
    line_number: int
    line_text: str


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
CaseSensitiveArg = Annotated[
    bool,
    Field(
        description="Match case exactly. Default is false (case-insensitive).",
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
    case_sensitive: bool,
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
            case_sensitive=case_sensitive,
        ):
            filename = str(Path(rg_match.path).relative_to(sp.path))
            if file_allowed(sp.filter_func, filename):
                matches.append(
                    GrepMatch(
                        filename=sp.prefixed(filename),
                        line_number=rg_match.line_number,
                        line_text=rg_match.line_text,
                    )
                )
    except Exception:
        logger.warning("Grep failed for pattern %r in %s", pattern, sp.path)
    return matches


@dataclass(slots=True, frozen=True)
class GrepTool(PathsTool):
    """Search documents for a pattern."""

    @override
    async def __call__(
        self,
        pattern: GrepPatternArg,
        path: GrepPathArg = None,
        context_lines: ContextLinesArg = 2,
        case_sensitive: CaseSensitiveArg = False,
        max_results: GrepMaxResultsArg = 50,
    ) -> ToolOutput[list[GrepMatch]]:
        """Search documents for a pattern.

        Searches case-insensitively by default.  Set ``case_sensitive=True``
        to match case exactly.
        """
        results = await asyncio.gather(
            *(
                _search_path(sp, pattern, path, context_lines, case_sensitive)
                for sp in self.resolved_paths
            )
        )
        all_matches = [m for batch in results for m in batch]
        all_matches = all_matches[:max_results]
        if not all_matches:
            return ToolOutput(data=all_matches, formatted="(no matches)")
        return ToolOutput(
            data=all_matches,
            formatted="\n".join(
                f"{m.filename}:{m.line_number}:{m.line_text}" for m in all_matches
            ),
        )
