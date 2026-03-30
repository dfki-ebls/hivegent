"""Typed async wrapper around ripgrep (``rg``)."""

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .base import run

__all__ = ["RgMatch", "rg_search"]


@dataclass(slots=True, frozen=True)
class RgMatch:
    """A single match block from ripgrep's JSON output.

    When context lines are requested, ``line_text`` contains the
    matched line together with its surrounding context,
    ``line_number`` refers to the first line of that block, and
    ``match_line`` is the line that actually matched the pattern.
    """

    path: str
    line_number: int
    line_text: str


async def rg_search(
    pattern: str,
    path: Path,
    *,
    glob: str | None = None,
    context_lines: int = 0,
    case_sensitive: bool = False,
) -> list[RgMatch]:
    """Search *path* for *pattern* using ripgrep.

    Args:
        pattern: Regex pattern to search for.
        path: Directory or file to search.
        glob: Only search files matching this glob (e.g. ``"*.md"``).
        context_lines: Number of context lines before and after each match.
        case_sensitive: When ``False`` (the default), search
            case-insensitively.  When ``True``, match case exactly.

    Returns:
        List of matches parsed from ripgrep's JSON output.
        When *context_lines* is positive, each match includes
        surrounding context in ``line_text``.
    """
    args: list[str | Path] = ["rg", "--json"]
    if not case_sensitive:
        args.append("--ignore-case")
    if glob:
        args.extend(["--glob", glob])
    if context_lines > 0:
        args.extend(["--context", str(context_lines)])
    args.extend([pattern, path])

    # rg exits 1 when there are no matches — that's not an error.
    result = await run(args, allowed_returncodes=(1,))

    matches: list[RgMatch] = []
    block_lines: list[tuple[int, str]] = []
    block_path: str | None = None

    def _flush() -> None:
        if block_lines and block_path is not None:
            matches.append(
                RgMatch(
                    path=block_path,
                    line_number=block_lines[0][0],
                    line_text="\n".join(text for _, text in block_lines),
                )
            )

    for item in result.stdout_ndjson():
        kind = item.get("type")
        if kind in ("begin", "end", "context_separator"):
            _flush()
            block_lines = []
            block_path = None
        elif kind == "match":
            data: dict[str, Any] = item["data"]
            # Without --context, consecutive matches have no separator,
            # so flush the previous block before starting a new one.
            if context_lines == 0:
                _flush()
                block_lines = []
            block_path = data["path"]["text"]
            block_lines.append(
                (data["line_number"], data["lines"]["text"].rstrip("\n"))
            )
        elif kind == "context":
            data = item["data"]
            block_lines.append(
                (data["line_number"], data["lines"]["text"].rstrip("\n"))
            )

    _flush()
    return matches
