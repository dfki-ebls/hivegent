"""Text-formatting primitives for LLM-facing output.

Block separators and line numbering live here so they stay consistent
wherever text is assembled for a model to read.
"""

from collections.abc import Iterable, Iterator, Sequence

__all__ = [
    "BLOCK_SEP",
    "GROUP_SEP",
    "annotate_lines",
    "cap_lines",
    "hint_suffix",
    "iter_annotated",
    "number_line",
    "truncate_block",
    "truncate_line",
    "truncate_middle",
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


def truncate_middle(text: str, max_chars: int) -> str:
    """Clip *text* to *max_chars*, preserving both ends exactly.

    >>> truncate_middle("abcdefghijklmnop", 15)
    'ab[truncated]op'
    """
    if len(text) <= max_chars:
        return text

    if max_chars <= 0:
        return ""

    label = "[truncated]"
    marker = label if max_chars > len(label) else ""
    remaining = max_chars - len(marker)
    leading = (remaining + 1) // 2
    trailing = remaining - leading
    suffix = text[-trailing:] if trailing else ""
    return f"{text[:leading]}{marker}{suffix}"


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


def cap_lines(
    lines: Iterable[str],
    max_chars: int | None = None,
    sep: str = "\n",
) -> tuple[str, int]:
    """Join *lines* with *sep* while the total stays within *max_chars*.

    Returns the joined text and how many lines were left out.  Once the
    budget is reached every remaining line is dropped, so what is kept is a
    contiguous prefix rather than whichever later lines happened to fit, and
    a caller can resume from exactly where the output stops.  The first line
    is always kept, however long it is: a lone oversized line tells the
    reader more than an empty result with a notice.

    This is a different axis from :func:`truncate_line`.  That one bounds a
    single runaway line; this one bounds what a whole tool return spends of
    the model's context, which line numbers and markup add to on top of the
    content the tool selected.

    >>> cap_lines(["ab", "cd", "ef"], 5)
    ('ab\\ncd', 1)
    >>> cap_lines(["abcdef"], 3)
    ('abcdef', 0)
    >>> cap_lines(["ab", "cd"])
    ('ab\\ncd', 0)
    """
    kept: list[str] = []
    total = 0
    iterator = iter(lines)

    for line in iterator:
        extra = len(line) + (len(sep) if kept else 0)

        if kept and max_chars is not None and total + extra > max_chars:
            return sep.join(kept), 1 + sum(1 for _ in iterator)

        kept.append(line)
        total += extra

    return sep.join(kept), 0


def hint_suffix(hints: Sequence[str]) -> str:
    """Render *hints* as the bracketed note that trails a tool's output.

    The one place the convention lives, so every tool that has to admit what
    its budgets left out admits it the same way.  No hints means no note, not
    an empty bracket.

    >>> hint_suffix(["3 more lines", "pass full_lines=true"])
    '\\n\\n[3 more lines; pass full_lines=true]'
    >>> hint_suffix([])
    ''
    """
    return f"\n\n[{'; '.join(hints)}]" if hints else ""


def iter_annotated(
    lines: Iterable[str],
    start_line: int = 1,
    max_line_chars: int | None = None,
) -> Iterator[str]:
    """Yield each of *lines* numbered from *start_line*, clipped per line.

    The line-at-a-time form of :func:`annotate_lines`, for callers that feed
    the result to :func:`cap_lines` and need to know how many lines the
    budget dropped.

    >>> list(iter_annotated(["a", "b"], start_line=4))
    ['4: a', '5: b']
    """
    for i, line in enumerate(lines):
        yield number_line(start_line + i, truncate_line(line, max_line_chars))


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
    :func:`cap_lines` is the counterpart budget on the output as a whole.

    >>> annotate_lines(["a", "b"], start_line=4)
    '4: a\\n5: b'
    >>> annotate_lines(["abcdef"], max_line_chars=3)
    '1: ab…'
    """
    return "\n".join(iter_annotated(lines, start_line, max_line_chars))
