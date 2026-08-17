"""Typed async wrapper around ripgrep (``rg``)."""

import base64
import logging
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
from .base import run

__all__ = ["RgLine", "RgMatch", "rg_search"]

logger = logging.getLogger(__name__)


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

        decoded = decode_bytes(raw)
        return decoded.text if decoded is not None else None


class _RgLineData(BaseModel):
    """The payload of a ``match`` or ``context`` event."""

    model_config = ConfigDict(frozen=True)

    path: _RgText
    lines: _RgText
    line_number: int


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

    for raw in result.stdout_ndjson():
        event = _parse_event(raw)
        if not isinstance(event, _RgLineEvent):
            if event is None:
                logger.debug("Skipping unmodelled ripgrep event: %r", raw)
            _flush()
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
        if is_match and context_lines == 0:
            _flush()
            block_lines = []
        block_path = line_path
        block_lines.append(
            RgLine(
                line_number=event.data.line_number,
                # Strip the terminator in both spellings: a CRLF source would
                # otherwise carry a carriage return into every formatted line.
                text=text.rstrip("\r\n"),
                is_match=is_match,
            )
        )

    _flush()
    return matches
