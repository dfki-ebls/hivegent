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
    "truncate_block",
    "truncate_line",
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


def truncate_line(text: str, max_chars: int | None = None) -> str:
    """Clip *text* to *max_chars* characters, marking any cut with an ellipsis.

    A ``None`` budget, or one at least as long as *text*, returns *text*
    unchanged.  This guards a single very long line — a base64-embedded
    image, a minified bundle — from flooding the model's context window when
    line-oriented output is assembled for it to read.

    >>> truncate_line("hello world", 8)
    'hello w…'
    >>> truncate_line("short", 100)
    'short'
    >>> truncate_line("untouched")
    'untouched'
    """
    if max_chars is None or len(text) <= max_chars:
        return text
    return text[: max_chars - 1] + "…"


def truncate_block(text: str, max_line_chars: int | None = None) -> str:
    """Clip every line of *text* to *max_line_chars*, rejoined by newlines.

    The counterpart to :func:`annotate_lines` for output that is not
    line-numbered: each line is clipped by :func:`truncate_line` so one very
    long line — a base64-embedded image, a minified bundle — cannot flood the
    model's context.  A ``None`` budget, or a *text* already within it, is
    returned unchanged without splitting.

    >>> truncate_block("ab\\ncdef", max_line_chars=3)
    'ab\\ncd…'
    >>> truncate_block("untouched")
    'untouched'
    """
    if max_line_chars is None or len(text) <= max_line_chars:
        return text
    return "\n".join(truncate_line(line, max_line_chars) for line in text.splitlines())


def annotate_lines(
    lines: Iterable[str],
    start_line: int = 1,
    max_line_chars: int | None = None,
) -> str:
    """Number *lines* sequentially from *start_line*, joined by newlines.

    Without per-line numbers an LLM can only see a chunk's overall line
    range and has to guess which line a sentence is on, producing off-by-one
    citations.  When *max_line_chars* is set, each line is first clipped by
    :func:`truncate_line` so one very long line cannot flood the context.

    >>> annotate_lines(["a", "b"], start_line=4)
    '4: a\\n5: b'
    >>> annotate_lines(["abcdef"], max_line_chars=3)
    '1: ab…'
    """
    return "\n".join(
        number_line(start_line + i, truncate_line(line, max_line_chars))
        for i, line in enumerate(lines)
    )
