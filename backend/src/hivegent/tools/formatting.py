"""Text-formatting primitives for LLM-facing output.

Block separators and line numbering live here so they stay consistent
wherever text is assembled for a model to read.
"""

from collections.abc import Iterable

__all__ = [
    "BLOCK_SEP",
    "GROUP_SEP",
    "annotate_lines",
    "number_line",
]

BLOCK_SEP = "\n---\n"
"""Separator placed between distinct blocks in formatted output."""

GROUP_SEP = "\n--\n"
"""Lighter rule than :data:`BLOCK_SEP` for sub-blocks under one heading.

Marks a gap between discontiguous runs that share a heading (e.g. several
match runs from the same document) without repeating the heading.
"""


def number_line(line_number: int, text: str, sep: str = ": ") -> str:
    """Prefix *text* with its 1-indexed *line_number*.

    *sep* sits between the number and the text. It defaults to ``": "`` for
    plain numbered output; tools that mark each line (e.g. grep, with ``:``
    for a match and ``-`` for context) pass their marker instead.

    >>> number_line(7, "hello")
    '7: hello'
    >>> number_line(7, "hit", ":")
    '7:hit'
    """
    return f"{line_number}{sep}{text}"


def annotate_lines(lines: Iterable[str], start_line: int = 1) -> str:
    """Number *lines* sequentially from *start_line*, joined by newlines.

    Without per-line numbers an LLM can only see a chunk's overall line
    range and has to guess which line a sentence is on, producing off-by-one
    citations.

    >>> annotate_lines(["a", "b"], start_line=4)
    '4: a\\n5: b'
    """
    return "\n".join(number_line(start_line + i, line) for i, line in enumerate(lines))
