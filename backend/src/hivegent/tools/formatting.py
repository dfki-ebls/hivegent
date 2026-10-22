"""Text-formatting primitives for LLM-facing output.

Block separators and line numbering live here so they stay consistent
wherever text is assembled for a model to read.
"""

import reprlib
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field

from ..humanize import pluralize

__all__ = [
    "BLOCK_SEP",
    "GROUP_SEP",
    "LineClip",
    "annotate_lines",
    "fit_lines",
    "hint_suffix",
    "iter_annotated",
    "number_line",
    "omission_hints",
    "render_arguments",
    "truncate_around",
    "truncate_line",
    "truncate_middle",
    "window_start",
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


def window_start(text: str, start: int, max_chars: int) -> int:
    """Where :func:`truncate_around` opens its window onto *text*.

    A quarter of the budget before *start*, so the lead-in reads too, and never
    past where the rest of *text* still fills the window.

    >>> window_start("a" * 100, 50, 20), window_start("a" * 100, 99, 20)
    (45, 80)
    """
    if len(text) <= max_chars:
        return 0

    return max(min(start - max_chars // 4, len(text) - max_chars), 0)


def truncate_around(text: str, start: int, max_chars: int) -> str:
    """Clip *text* to *max_chars* characters kept around offset *start*.

    For a line whose point of interest, such as a search hit, may sit far past
    what :func:`truncate_line` keeps of its head.  The window opens at
    :func:`window_start`, and each cut side is marked with an ellipsis.

    >>> truncate_around("a" * 20 + "hit" + "b" * 20, 20, 12)
    '…aahitbbbbb…'
    >>> truncate_around("hit and more", 0, 8)
    'hit and…'
    """
    begin = window_start(text, start, max_chars)

    if not begin:
        return truncate_line(text, max_chars)

    # The leading ellipsis takes the place of the first character kept, and
    # one character past the window is enough to tell whether the tail is cut.
    return "…" + truncate_line(text[begin + 1 : begin + max_chars + 1], max_chars - 1)


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
        return (
            note(head) if note is not None else f"[{left_out} characters left out here]"
        )

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


@dataclass(slots=True)
class LineClip:
    """A per-line budget that clips each line it is handed and names any cut.

    A clipped line is the one cut a reader cannot tell from the output alone,
    so one object both clips and remembers, and :meth:`hints` admits it.  A
    ``None`` budget keeps every line whole, which is what ``full_lines`` asks
    for, so the hint names that argument.

    >>> clip = LineClip(4)
    >>> clip("ab"), clip("abcdef"), clip.hints()
    ('ab', 'abc…', ['lines clipped at 4 chars, pass full_lines=true to see them whole'])
    >>> LineClip(None)("abcdef")
    'abcdef'
    """

    max_chars: int | None
    noun: str = "lines"
    clipped: bool = field(default=False, init=False)

    def __call__(self, text: str) -> str:
        """Clip *text* from its start."""
        return self.around(text, 0)

    def around(self, text: str, start: int) -> str:
        """Clip *text* around offset *start*, as :func:`truncate_around` does."""
        if self.max_chars is None or len(text) <= self.max_chars:
            return text

        self.clipped = True

        return truncate_around(text, start, self.max_chars)

    def hints(self) -> list[str]:
        """The hint naming the cut, or none when no line was clipped."""
        if not self.clipped:
            return []

        return [
            (
                f"{self.noun} clipped at {self.max_chars} chars, "
                "pass full_lines=true to see them whole"
            )
        ]


def fit_lines(lines: Iterable[str], max_chars: int) -> int:
    """How many of *lines*, joined by newlines, fit within *max_chars*.

    The lines that fit are a contiguous prefix rather than whichever later
    lines happened to fit, so a caller can resume from exactly where the
    output stops, and nothing past the first line that overflows is read.  The
    first line counts however long it is, since a lone oversized line can tell
    the reader more than an empty result with a notice.

    This is a different axis from :class:`LineClip`, which bounds a single
    runaway line.

    >>> fit_lines(["ab", "cd", "ef"], 5)
    2
    >>> fit_lines(["abcdef"], 3)
    1
    """
    count = 0
    # The first line takes no newline before it.
    total = -1

    for line in lines:
        total += len(line) + 1

        if count and total > max_chars:
            break

        count += 1

    return count


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
    clip: LineClip | None = None,
) -> Iterator[str]:
    """Yield each of *lines* numbered from *start_line*, clipped per line.

    The line-at-a-time form of :func:`annotate_lines`, for callers that join
    the numbered lines themselves.

    >>> list(iter_annotated(["a", "b"], start_line=4))
    ['4: a', '5: b']
    """
    for i, line in enumerate(lines):
        yield number_line(start_line + i, line if clip is None else clip(line))


def annotate_lines(
    lines: Iterable[str],
    start_line: int = 1,
    clip: LineClip | None = None,
) -> str:
    """Number *lines* sequentially from *start_line*, joined by newlines.

    Without per-line numbers an LLM can only see a chunk's overall line
    range and has to guess which line a sentence is on, producing off-by-one
    citations.  When *clip* is given, each line is first clipped by it so one
    very long line cannot flood the context.  :func:`fit_lines` is the
    counterpart budget on the output as a whole.

    >>> annotate_lines(["a", "b"], start_line=4)
    '4: a\\n5: b'
    >>> annotate_lines(["abcdef"], clip=LineClip(3))
    '1: ab…'
    """
    return "\n".join(iter_annotated(lines, start_line, clip))
