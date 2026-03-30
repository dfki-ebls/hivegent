"""Grep tool callable — search documents for a pattern."""

import asyncio
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, override

from pydantic import Field

from ..subprocesses import rg_search
from .base import PathsTool, SearchPath, file_allowed

__all__ = [
    "CaseSensitiveArg",
    "ContextLinesArg",
    "GrepMatch",
    "GrepGlobArg",
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
GrepGlobArg = Annotated[
    str | None,
    Field(description="Optional glob pattern that limits which files are searched."),
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


async def _search_path(
    sp: SearchPath,
    pattern: str,
    glob: str | None,
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
            glob=glob,
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
        glob: GrepGlobArg = None,
        context_lines: ContextLinesArg = 2,
        case_sensitive: CaseSensitiveArg = False,
    ) -> list[GrepMatch]:
        """Search documents for a pattern.

        Searches case-insensitively by default.  Set ``case_sensitive=True``
        to match case exactly.
        """
        results = await asyncio.gather(
            *(
                _search_path(sp, pattern, glob, context_lines, case_sensitive)
                for sp in self.resolved_paths
            )
        )
        return [m for batch in results for m in batch]
