"""Typed async wrapper around ripgrep (``rg``)."""

from dataclasses import dataclass
from pathlib import Path

from ._base import run

__all__ = ["RgMatch", "RgSubMatch", "rg_search"]


@dataclass(slots=True, frozen=True)
class RgSubMatch:
    """A submatch within a ripgrep match line."""

    text: str
    start: int
    end: int


@dataclass(slots=True, frozen=True)
class RgMatch:
    """A single match from ripgrep's JSON output."""

    path: str
    line_number: int
    line_text: str
    submatches: tuple[RgSubMatch, ...]


async def rg_search(
    pattern: str,
    path: Path,
    *,
    glob: str | None = None,
    context_lines: int = 0,
    smart_case: bool = True,
) -> list[RgMatch]:
    """Search *path* for *pattern* using ripgrep.

    Args:
        pattern: Regex pattern to search for.
        path: Directory or file to search.
        glob: Only search files matching this glob (e.g. ``"*.md"``).
        context_lines: Number of context lines before and after each match.
        smart_case: Enable smart-case matching (case-insensitive unless
            the pattern contains uppercase letters).

    Returns:
        List of matches parsed from ripgrep's JSON output.
    """
    args: list[str | Path] = ["rg", "--json"]
    if smart_case:
        args.append("--smart-case")
    if glob:
        args.extend(["--glob", glob])
    if context_lines > 0:
        args.extend(["--context", str(context_lines)])
    args.extend([pattern, path])

    # rg exits 1 when there are no matches — that's not an error.
    result = await run(args, allowed_returncodes=(1,))

    matches: list[RgMatch] = []
    for item in result.stdout_ndjson():
        if item.get("type") != "match":
            continue
        data = item["data"]
        submatches = tuple(
            RgSubMatch(
                text=sm["match"]["text"],
                start=sm["start"],
                end=sm["end"],
            )
            for sm in data.get("submatches", ())
        )
        matches.append(
            RgMatch(
                path=data["path"]["text"],
                line_number=data["line_number"],
                line_text=data["lines"]["text"].rstrip("\n"),
                submatches=submatches,
            )
        )
    return matches
