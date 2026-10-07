"""Text-formatting primitives for LLM-facing output.

Block separators and line numbering live here so they stay consistent
wherever text is assembled for a model to read.
"""

import reprlib
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence

from ..humanize import pluralize

__all__ = [
    "BLOCK_SEP",
    "GROUP_SEP",
    "annotate_lines",
    "cap_lines",
    "hint_suffix",
    "iter_annotated",
    "number_line",
    "omission_hints",
    "render_arguments",
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
    unchanged, and one below a character returns nothing.  This guards a
    single very long line — a base64-embedded image, a minified bundle — from
    flooding the model's context window when line-oriented output is assembled
    for it to read.

    >>> truncate_line("hello world", 8)
    'hello w…'
    >>> truncate_line("short", 100)
    'short'
    >>> truncate_line("untouched")
    'untouched'
    """
    if max_chars is None or len(text) <= max_chars:
        return text

    return text[: max_chars - 1] + "…" if max_chars > 0 else ""


def _head_and_tail(text: str, room: int) -> tuple[str, str]:
    """The head and tail of *text* that fit *room*, cut at line ends where there are any.

    >>> _head_and_tail("line1\\nline2\\nline3\\nline4", 17)
    ('line1\\nline2', 'line4')
    """
    head_room = room * 3 // 4
    tail_start = len(text) - (room - head_room)
    end = text.rfind("\n", 0, head_room + 1)
    start = text.find("\n", tail_start - 1)
    head = text[:end] if end > 0 else text[:head_room]
    tail = text[start + 1 :] if start >= 0 else text[tail_start:]

    return head, tail


def truncate_middle(
    text: str, max_chars: int, note: Callable[[str], str] | None = None
) -> str:
    """Keep the head and tail of *text* within *max_chars*, around a note on the rest.

    The head takes three parts of the room to the tail's one, both cut at line
    ends, and the tail keeps the hints a tool appends to its output.  *note*
    renders what stands between them from the head it follows, and by default
    counts the characters left out.  The note is never cut, since it may name
    where the rest is, so the room is what is left once it is sized for the
    whole text as its head, which a note must not outgrow as its head shrinks.
    Where even the note does not fit, it is all that is returned, clipped.

    >>> print(truncate_middle("\\n".join(map(str, range(100))), 60))
    0
    1
    2
    3
    4
    5
    6
    7
    8
    9
    [265 characters left out here]
    98
    99
    """
    if len(text) <= max_chars:
        return text

    def marker(head: str, left_out: int) -> str:
        return note(head) if note is not None else f"[{left_out} characters left out here]"

    # Two line breaks join the note to its head and tail.
    room = max_chars - len(marker(text, len(text))) - 2

    if room < 0:
        return truncate_line(marker("", len(text)), max_chars)

    head, tail = _head_and_tail(text, room)
    middle = marker(head, len(text) - len(head) - len(tail))

    return "\n".join(filter(None, (head, middle, tail)))


_ARGUMENT_REPR = reprlib.Repr(maxlevel=2, maxstring=200)
"""Renders one argument value cut short, never the whole of a large one."""


def render_arguments(args: str | Mapping[str, object] | None, max_chars: int) -> str:
    """The keyword arguments of a call as it spells them, within *max_chars*.

    A large argument, such as a written document or a program, is cut while it
    is rendered rather than serialized whole and cut afterwards.  A string is
    the arguments a model sent already serialized.

    >>> render_arguments({"query": "x", "limit": 5}, 200)
    "query='x', limit=5"
    """
    text = (
        args
        if isinstance(args, str)
        else ", ".join(
            f"{key}={_ARGUMENT_REPR.repr(value)}" for key, value in (args or {}).items()
        )
    )

    return truncate_line(text, max_chars)


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


def cap_lines(lines: Iterable[str], max_chars: int | None = None) -> tuple[str, int]:
    """Join *lines* with newlines while the total stays within *max_chars*.

    Returns the joined text and how many lines were left out.  Once the
    budget is reached every remaining line is dropped, so what is kept is a
    contiguous prefix rather than whichever later lines happened to fit, and
    a caller can resume from exactly where the output stops.  The first line
    is kept however long it is, since a lone oversized line can tell the
    reader more than an empty result with a notice.

    This is a different axis from :func:`truncate_line`, which bounds a
    single runaway line.

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
        extra = len(line) + (1 if kept else 0)

        if kept and max_chars is not None and total + extra > max_chars:
            return "\n".join(kept), 1 + sum(1 for _ in iterator)

        kept.append(line)
        total += extra

    return "\n".join(kept), 0


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


def omission_hints(
    *,
    hidden: int = 0,
    deeper: int = 0,
    max_depth: int | None = None,
    max_results: int | None = None,
    shown: int = 0,
    total: int | None = None,
) -> list[str]:
    """Say what a search or listing left out, so a short result is not read as all.

    Each hint is what changing one argument would reveal: *hidden* entries
    ``include_ignored`` would expose, *deeper* ones a larger *max_depth* would,
    and the rest past *max_results*.  A call that knows its *total* says how
    many there are, and one that stopped walking once *shown* reached the cap
    can only say there may be more.  Render them with :func:`hint_suffix`.

    >>> omission_hints(hidden=1, max_results=5, total=9)
    ['1 hidden entry (`.assets` contents and common build/vendor directories), pass include_ignored=True to reveal them', 'showing 5 of 9, raise max_results to see more']
    >>> omission_hints(max_results=5, shown=5)
    ['reached max_results=5, there may be more']
    """
    hints: list[str] = []

    if hidden:
        noun = pluralize(hidden, "entry", "entries")
        hints.append(
            f"{hidden} hidden {noun} (`.assets` contents and common build/vendor "
            "directories), pass include_ignored=True to reveal them"
        )

    if deeper:
        noun = pluralize(deeper, "entry", "entries")
        hints.append(
            f"{deeper} {noun} below max_depth={max_depth}, raise it or pass a "
            "directory as path to see them"
        )

    if max_results is None:
        return hints

    if total is not None and total > max_results:
        hints.append(f"showing {max_results} of {total}, raise max_results to see more")
    elif total is None and shown >= max_results:
        hints.append(f"reached max_results={max_results}, there may be more")

    return hints


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
