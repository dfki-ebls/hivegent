"""Grep tool callable — search documents for a pattern."""

import logging
from dataclasses import dataclass
from pathlib import Path

from typing import override

from ..subprocesses import rg_search
from .typing import GrepMatch, Tool

__all__ = ["GrepTool"]

logger = logging.getLogger(__name__)


@dataclass(slots=True, frozen=True)
class GrepTool(Tool):
    """Search documents for a pattern."""

    path: Path

    @override
    async def __call__(
        self,
        pattern: str,
        glob: str | None = None,
        context_lines: int = 0,
    ) -> list[GrepMatch]:
        """Search documents for a pattern.

        Uses smart case matching: case-insensitive unless the pattern contains
        uppercase letters.

        Args:
            pattern: Text or regex pattern to search for.
            glob: Only search files matching this pattern (e.g., "*.md", "notes/*").
            context_lines: Number of lines to show before and after each match.
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
