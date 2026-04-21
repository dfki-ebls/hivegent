"""Typed async wrapper around ripgrep (``rg``)."""

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .base import run

__all__ = ["RgLine", "RgMatch", "rg_search"]


@dataclass(slots=True, frozen=True)
class RgLine:
    """A single line in a match block with its match/context classification."""

    line_number: int
    text: str
    is_match: bool


@dataclass(slots=True, frozen=True)
class RgMatch:
    """A match block from ripgrep — at least one matching line, plus any
    surrounding context lines that ripgrep grouped with it."""

    path: str
    lines: tuple[RgLine, ...]


async def rg_search(
    pattern: str,
    path: Path,
    *,
    glob: str | None = None,
    context_lines: int = 0,
    case_sensitive: bool = False,
    literal: bool = False,
    exclude_dirs: tuple[str, ...] = (),
) -> list[RgMatch]:
    """Search *path* for *pattern* using ripgrep.

    Args:
        pattern: Regex pattern to search for.
        path: Directory or file to search.
        glob: Only search files matching this glob (e.g. ``"*.md"``).
        context_lines: Number of context lines before and after each match.
        case_sensitive: When ``False`` (the default), search
            case-insensitively.  When ``True``, match case exactly.
        literal: When ``True``, treat *pattern* as a fixed string rather
            than a regular expression.
        exclude_dirs: Directory names to skip anywhere in the tree
            (e.g. ``("node_modules", ".git")``).

    Returns:
        List of match blocks parsed from ripgrep's JSON output.  Each
        block contains one or more lines; ``is_match`` distinguishes
        matching lines from surrounding context.
    """
    args: list[str | Path] = ["rg", "--json"]
    if not case_sensitive:
        args.append("--ignore-case")
    if literal:
        args.append("--fixed-strings")
    if glob:
        args.extend(["--glob", glob])
    for excluded in exclude_dirs:
        args.extend(["--glob", f"!**/{excluded}/**"])
    if context_lines > 0:
        args.extend(["--context", str(context_lines)])
    args.extend([pattern, path])

    # rg exits 1 when there are no matches — that's not an error.
    result = await run(args, allowed_returncodes=(1,))

    matches: list[RgMatch] = []
    block_lines: list[RgLine] = []
    block_path: str | None = None

    def _flush() -> None:
        if block_lines and block_path is not None:
            matches.append(RgMatch(path=block_path, lines=tuple(block_lines)))

    for item in result.stdout_ndjson():
        kind = item.get("type")
        if kind in ("begin", "end", "context_separator"):
            _flush()
            block_lines = []
            block_path = None
        elif kind in ("match", "context"):
            data: dict[str, Any] = item["data"]
            # Without --context, consecutive matches have no separator,
            # so flush the previous block before starting a new one.
            if kind == "match" and context_lines == 0:
                _flush()
                block_lines = []
            block_path = data["path"]["text"]
            block_lines.append(
                RgLine(
                    line_number=data["line_number"],
                    text=data["lines"]["text"].rstrip("\n"),
                    is_match=(kind == "match"),
                )
            )

    _flush()
    return matches
