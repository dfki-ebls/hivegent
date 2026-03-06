"""Grep tool callable — search documents for a pattern."""

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, override

from pydantic import Field

from ..subprocesses import rg_search
from .base import Tool

__all__ = [
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


@dataclass(slots=True, frozen=True)
class GrepTool(Tool):
    """Search documents for a pattern."""

    path: Path

    @override
    async def __call__(
        self,
        pattern: GrepPatternArg,
        glob: GrepGlobArg = None,
        context_lines: ContextLinesArg = 0,
    ) -> list[GrepMatch]:
        """Search documents for a pattern.

        Uses smart case matching: case-insensitive unless the pattern contains
        uppercase letters.
        """
        if not self.path.exists():
            return []

        matches: list[GrepMatch] = []
        try:
            for rg_match in await rg_search(
                pattern,
                self.path,
                glob=glob,
                context_lines=context_lines,
            ):
                filename = str(Path(rg_match.path).relative_to(self.path))
                matches.append(
                    GrepMatch(
                        filename=filename,
                        line_number=rg_match.line_number,
                        line_text=rg_match.line_text,
                    )
                )
        except Exception:
            logger.warning("Grep failed for pattern %r in %s", pattern, self.path)

        return matches
