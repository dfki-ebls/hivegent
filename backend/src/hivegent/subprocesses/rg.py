"""Typed async wrapper around ripgrep (``rg``)."""

import base64
import json
import logging
from collections.abc import AsyncGenerator
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    TypeAdapter,
    ValidationError,
)

from ..text import decode_bytes
from .base import run, stream_lines

__all__ = ["RgLine", "RgMatch", "rg_count", "rg_search"]

logger = logging.getLogger(__name__)


@dataclass(slots=True, frozen=True)
class RgLine:
    """A single line in a match block with its match/context classification."""

    line_number: int
    text: str
    is_match: bool
    match_start: int | None = None
    """Character offset of the first match in *text*, ``None`` for context."""


@dataclass(slots=True, frozen=True)
class RgMatch:
    """A match block from ripgrep — at least one matching line, plus any
    surrounding context lines that ripgrep grouped with it."""

    # Relative to the search root, which is where ripgrep was run.
    path: str
    lines: tuple[RgLine, ...]


def _decode(raw: bytes) -> str | None:
    """Bytes ripgrep reported as text, ``None`` where they are not text at all."""
    decoded = decode_bytes(raw)
    return decoded.text if decoded is not None else None


class _RgText(BaseModel):
    """ripgrep's "arbitrary data" object, in either form it is emitted in.

    ripgrep fills in ``text`` only for valid UTF-8 and falls back to base64
    ``bytes`` for everything else, such as a line out of a Latin-1 file or a
    filename the filesystem never validated.
    """

    model_config = ConfigDict(frozen=True)

    text: str | None = None
    bytes: str | None = None

    def decode(self) -> str | None:
        """Return the value as text, decoding the base64 fallback.

        The fallback goes through the shared decoder, so legacy Western
        content reads back as text instead of being lost.  ``None`` means
        neither form was filled in, or the bytes are not text at all.
        """
        if self.text is not None:
            return self.text
        if self.bytes is None:
            return None

        try:
            raw = base64.b64decode(self.bytes)
        except ValueError:
            return None

        return _decode(raw)


class _RgSubmatch(BaseModel):
    """Where one match sits in its line, as a byte offset."""

    model_config = ConfigDict(frozen=True)

    start: int


class _RgLineData(BaseModel):
    """The payload of a ``match`` or ``context`` event."""

    model_config = ConfigDict(frozen=True)

    path: _RgText
    lines: _RgText
    line_number: int
    submatches: tuple[_RgSubmatch, ...] = ()

    def match_start(self) -> int | None:
        """The first match's offset in characters rather than ripgrep's bytes.

        ``None`` for a context line, and for a line ripgrep could only hand
        over as bytes, whose decoded characters no longer line up with them.
        """
        if not self.submatches or self.lines.text is None:
            return None

        start = self.submatches[0].start

        if self.lines.text.isascii():
            return start

        return len(self.lines.text.encode()[:start].decode(errors="ignore"))


class _RgLineEvent(BaseModel):
    """An event carrying one matching or surrounding line."""

    model_config = ConfigDict(frozen=True)

    type: Literal["match", "context"]
    data: _RgLineData


class _RgBoundaryEvent(BaseModel):
    """An event that only delimits blocks, whose payload is of no interest."""

    model_config = ConfigDict(frozen=True)

    type: Literal["begin", "end", "context_separator", "summary"]


type _RgEvent = Annotated[_RgLineEvent | _RgBoundaryEvent, Field(discriminator="type")]

_EVENTS: TypeAdapter[_RgEvent] = TypeAdapter(_RgEvent)


def _parse_event(raw: JsonValue) -> _RgEvent | None:
    """Validate one decoded JSON line into a typed event.

    ``None`` for anything this wrapper does not model, which the caller
    skips: an event a future ripgrep adds, or a record whose payload does not
    fit, costs that line rather than every result of the search.
    """
    try:
        return _EVENTS.validate_python(raw)
    except ValidationError:
        return None


def _filters(
    *,
    glob: str | None,
    case_sensitive: bool,
    literal: bool,
    exclude_dirs: tuple[str, ...],
) -> list[str]:
    """The flags a count and a search share: how the pattern matches and what is walked."""
    args: list[str] = []
    if not case_sensitive:
        args.append("--ignore-case")
    if literal:
        args.append("--fixed-strings")
    if glob:
        args.extend(["--glob", glob])
    for excluded in exclude_dirs:
        args.extend(["--glob", f"!**/{excluded}/**"])
    return args


async def rg_count(
    pattern: str,
    root: Path,
    *,
    glob: str | None = None,
    case_sensitive: bool = False,
    literal: bool = False,
    exclude_dirs: tuple[str, ...] = (),
) -> dict[str, int]:
    """Count the matching lines of every file below *root*, by path relative to it.

    The whole result set in a line per file, however much matches.  The
    arguments are :func:`rg_search`'s.
    """
    args: list[str | Path] = ["rg", "--count", "--null"]
    args.extend(
        _filters(
            glob=glob,
            case_sensitive=case_sensitive,
            literal=literal,
            exclude_dirs=exclude_dirs,
        )
    )
    args.extend(["--", pattern, "."])
    # rg exits 1 when there are no matches — that's not an error.
    result = await run(args, allowed_returncodes=(1,), cwd=root)

    counts: dict[str, int] = {}

    for line in result.stdout.splitlines():
        raw, _, count = line.partition(b"\0")

        if (path := _decode(raw)) is not None:
            counts[path.removeprefix("./")] = int(count)

    return counts


async def rg_search(
    pattern: str,
    root: Path,
    *,
    glob: str | None = None,
    context_lines: int = 0,
    max_count: int | None = None,
    case_sensitive: bool = False,
    literal: bool = False,
    exclude_dirs: tuple[str, ...] = (),
) -> AsyncGenerator[RgMatch]:
    """Search *root* for *pattern* using ripgrep, yielding match blocks as they arrive.

    The files are walked in path order, so the blocks a caller stops after
    are the same on every run, and closing the generator stops ripgrep, so a
    caller that has enough never pays for the rest of the walk.

    ripgrep runs with *root* as its working directory rather than being
    handed it as an argument, because that is what anchors a glob: ripgrep
    matches a glob carrying a slash against the path relative to the working
    directory, so ``reports/*.txt`` passed beside an absolute root was tested
    against ``/…/workspace/user:x/reports/note.txt`` and could never match,
    while a slashless ``*.md`` matched by basename and appeared to work.  The
    reported paths come back relative to *root* for the same reason.

    Args:
        pattern: Regex pattern to search for.
        root: Directory to search, which is also what every glob and every
            reported path is relative to.
        glob: Only search files matching this glob, either by basename
            (``"*.md"``) or anchored at *root* (``"reports/*.txt"``).
        context_lines: Number of context lines before and after each match.
        max_count: Most matching lines to report per file, all for ``None``.
        case_sensitive: When ``False`` (the default), search
            case-insensitively.  When ``True``, match case exactly.
        literal: When ``True``, treat *pattern* as a fixed string rather
            than a regular expression.
        exclude_dirs: Directory names to skip anywhere in the tree
            (e.g. ``("node_modules", ".git")``).

    Yields:
        Match blocks parsed from ripgrep's JSON output, each path relative to
        *root*.  Each block contains one or more lines, and ``is_match``
        distinguishes matching lines from surrounding context.
    """
    args: list[str | Path] = ["rg", "--json", "--sort", "path"]
    args.extend(
        _filters(
            glob=glob,
            case_sensitive=case_sensitive,
            literal=literal,
            exclude_dirs=exclude_dirs,
        )
    )
    if context_lines > 0:
        args.extend(["--context", str(context_lines)])
    if max_count is not None:
        args.extend(["--max-count", str(max_count)])
    args.extend(["--", pattern, "."])

    block_lines: list[RgLine] = []
    block_path: str | None = None

    # rg exits 1 when there are no matches — that's not an error.  An
    # explicitly given target is searched whatever the ignore files say, and
    # `.` is as explicit as an absolute path, so a workspace under an ignored
    # directory (`data/` in the dev tree) stays searchable.
    async for line in stream_lines(args, allowed_returncodes=(1,), cwd=root):
        if not line.strip():
            continue

        raw = json.loads(line)
        event = _parse_event(raw)
        if not isinstance(event, _RgLineEvent):
            if event is None:
                logger.debug("Skipping unmodelled ripgrep event: %r", raw)
            if block_lines and block_path is not None:
                yield RgMatch(path=block_path, lines=tuple(block_lines))
            block_lines = []
            block_path = None
            continue

        line_path = event.data.path.decode()
        text = event.data.lines.decode()
        if line_path is None or text is None:
            logger.debug("Skipping undecodable ripgrep line: %r", raw)
            continue

        is_match = event.type == "match"
        # Without --context, consecutive matches have no separator,
        # so flush the previous block before starting a new one.
        if is_match and context_lines == 0 and block_lines and block_path is not None:
            yield RgMatch(path=block_path, lines=tuple(block_lines))
            block_lines = []
        # `.` is the target, so every path arrives spelled `./<relative>`.
        block_path = line_path.removeprefix("./")
        block_lines.append(
            RgLine(
                line_number=event.data.line_number,
                # Strip the terminator in both spellings: a CRLF source would
                # otherwise carry a carriage return into every formatted line.
                text=text.rstrip("\r\n"),
                is_match=is_match,
                match_start=event.data.match_start(),
            )
        )

    if block_lines and block_path is not None:
        yield RgMatch(path=block_path, lines=tuple(block_lines))
