"""Redirecting a tool's result into a workspace file instead of the context.

A tool whose answer can dwarf the question is worth calling anyway when a
later step, not the model's own reading, is what turns the result into an
answer.  ``output_path`` is what makes that call affordable: the result is
committed through the canonical mutation gateway and the model gets back a
receipt naming the file, which a ``run_python`` program then opens on the
mounted workspace.  A result short enough to read is repeated on the receipt,
since withholding it saves nothing.

The suffix picks the channel, because the two a tool returns are not
interchangeable: ``.json`` writes the structured ``data`` — every grep match,
not the first ``max_results`` of them — while ``.txt`` writes the very text the
model would otherwise have been shown.

The argument is declared by each tool that offers it, next to a ``sink``
field, the way :class:`~hivegent.tools.python.RunPythonTool` declares a
writer for the one document its programs persist.  Nothing injects it: where
a result may land is a property of the tool as it was built for a run, so a
surface that hands out no sink leaves it out of what it builds
(:meth:`~hivegent.tools.base.ToolSpec.without`) rather than advertising an
argument it could only refuse.
"""

from abc import ABC
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Annotated, Any, Literal, cast

from pydantic import Field
from pydantic_core import to_json

from ..humanize import pluralize
from .base import AsyncPathTool, AsyncTool, ToolOutput, ToolRetry, Unreachable
from .mutations import WriteDocumentTool, resolve_text_target

__all__ = [
    "NO_WRITER_REFUSAL",
    "OutputFormat",
    "OutputPathArg",
    "OutputSink",
    "RedirectedOutput",
    "RedirectingPathTool",
    "RedirectingTool",
    "output_format",
    "redirect_output",
    "resolve_output_target",
]

type OutputFormat = Literal["json", "txt"]
"""Which of a tool result's two channels a redirect writes."""

_FORMATS: dict[str, OutputFormat] = {".json": "json", ".txt": "txt"}
"""The suffixes a redirect accepts, mapped to the channel each one names."""


@dataclass(slots=True, frozen=True)
class OutputSink:
    """Where a redirect commits, and how much of a result it still shows.

    *inline_chars* is the longest result text a receipt repeats rather than
    withholds.  It exists because a redirect pays off only on a result too
    large to read, and one shorter costs less in context than a receipt the
    model then misreads: told only that a listing held three entries, it
    concluded a directory was empty.
    """

    writer: WriteDocumentTool
    inline_chars: int


@dataclass(slots=True, frozen=True)
class RedirectedOutput:
    """The receipt a redirected call returns in place of its result."""

    output_path: str
    format: OutputFormat
    characters: int
    entries: int | None = None
    """Top-level entries when the payload is a sequence, ``None`` otherwise."""

    truncated: bool = False
    """Whether the result written was already a cut of what was asked for.

    A redirect hands back a size and nothing else, so a cut the tool knew about
    dies here unless it is carried: a query capped at its row limit wrote a
    partial table under a receipt that read like a whole one, and the run
    computed from the file believing it had everything.
    """


OutputPathArg = Annotated[
    str | None,
    Field(
        description=(
            "Leave unset to get the result back. Set it only to write the "
            "result to this file instead and get back just a receipt, never "
            "to choose what the call reads. A full workspace path ending in "
            "`.json` (structured result) or `.txt` (the text you would see), "
            "such as `~/.scratch/result.json`."
        ),
    ),
    Unreachable(RedirectedOutput),
]
"""The redirect argument, worded for the several tools that each declare it.

It sits next to input arguments such as ``path``, so it opens by saying it is
optional and where the result goes, and closes on a complete example path:
a model that mistook it for an input, or guessed at its form, wasted a retry
on each of the suffix and the scope prefix.  When to reach for it is worth a
paragraph, which a per-tool restatement would cost on every request, so that
stays in ``REDIRECT_INSTRUCTIONS``, composed once for the whole run.
"""


NO_WRITER_REFUSAL = (
    "Writing to the workspace is not available in this chat mode, so "
    "`output_path` cannot be used."
)
"""The one refusal a path cannot explain: this run may not write at all.

Shared because ``run_python`` reaches the same wall for its own declared
output, and a mode that cannot write should not answer two different ways
depending on which argument asked.
"""


def resolve_output_target(
    writer: WriteDocumentTool | None, output_path: str
) -> tuple[WriteDocumentTool, str, Path]:
    """Resolve one writable output, which need not exist yet.

    Routed through the resolver the commit itself runs through, so a path the
    write would turn away — a directory or a binary format included — is turned
    away here, in the same words, and before the tool has done its work.  A
    missing *writer* is the tool saying it was not built to write at all, and
    the writer comes back with the resolved path so a caller has it in hand
    rather than re-proving it.
    """
    if writer is None:
        raise ToolRetry(NO_WRITER_REFUSAL)

    canonical, _local, absolute = resolve_text_target(
        writer.resolved_paths, output_path
    )

    return writer, canonical, absolute


def output_format(output_path: str) -> OutputFormat:
    """Read the requested channel off the output path's suffix.

    Public because the agent's argument validator runs it before the call, so
    an unusable suffix costs a correction rather than a whole search, fetch, or
    scan thrown away once it has already run.  Canonicalising a path cannot
    change its suffix, so the early answer is the final one.
    """
    fmt = _FORMATS.get(PurePosixPath(output_path).suffix.lower())
    if fmt is None:
        raise ToolRetry(
            f"'{output_path}' must end in `.json` or `.txt`: the suffix "
            "chooses what is written, `.json` the structured result and `.txt` "
            "the text you would have been shown."
        )

    return fmt


def _render(result: ToolOutput[Any], fmt: OutputFormat) -> str:
    """Serialise the channel *fmt* names.

    The JSON is unindented: its declared reader is ``json.loads`` inside a
    ``run_python`` sandbox, and indentation would inflate a nested result
    several times over in the bytes written, stored, and — outside a
    `.scratch/` directory — chunked and embedded.  Rendering for a human to
    read is what the `.txt` channel is for.

    This renders the payload as it stands, while the sandbox renders it through
    the adapter for the result type the tool declares, so the two surfaces stop
    at different points, bytes here and objects there, and agree on the shape
    of a declared result without either passing through the other.
    """
    if fmt == "txt":
        return result.text

    return to_json(result.data).decode()


def _receipt[T](
    report: str,
    canonical_path: str,
    fmt: OutputFormat,
    content: str,
    result: ToolOutput[T],
    inline_chars: int,
) -> ToolOutput[RedirectedOutput]:
    """Report what was written, repeating the result's text only when short.

    *report* is what the write gateway said it did, so the sentence describing
    a workspace write is composed in one place and this adds only what is
    particular to a redirect.
    """
    data = result.data
    entries = len(data) if isinstance(data, list | tuple) else None
    counted = f", {entries} {pluralize(entries, 'entry', 'entries')}" if entries else ""
    # Duck-typed rather than narrowed to one result: every payload that knows
    # it was cut spells it the same way, and a receipt that drops the fact is
    # the one place the model cannot recover it from.
    truncated = bool(getattr(data, "truncated", False))
    partial = (
        " It is what the call returned, which was already cut short of what "
        "you asked for — raise the limit or narrow the request if you need "
        "the rest."
        if truncated
        else ""
    )
    # The `.txt` channel already is the text, so it is not rendered twice.
    text = content if fmt == "txt" else result.text
    short = len(text) <= inline_chars
    clause = (
        "which is short enough to show here as well"
        if short
        else "and is not repeated here"
    )
    shown = f"\n\n{text}" if short else ""

    return ToolOutput(
        data=RedirectedOutput(
            output_path=canonical_path,
            format=fmt,
            characters=len(content),
            entries=entries,
            truncated=truncated,
        ),
        formatted=(
            f"{report} It holds this call's result as {fmt}{counted}, "
            f"{clause}.{partial}{shown}"
        ),
    )


async def redirect_output[T](
    result: ToolOutput[T],
    output_path: str | None,
    sink: OutputSink | None,
) -> ToolOutput[T | RedirectedOutput]:
    """Commit *result* to *output_path*, or pass it through when none was named.

    The write lands on the canonical mutation path, so a redirect is indexed,
    locked, and announced exactly like any other document write — and skips all
    three under a `.scratch/` directory, which is where a redirect belongs.
    """
    # `ToolOutput` is invariant in its payload, so widening either branch to
    # the declared union is a cast rather than a subtype step.
    if output_path is None:
        return cast(ToolOutput[T | RedirectedOutput], result)

    fmt = output_format(output_path)
    if sink is None:
        raise ToolRetry(NO_WRITER_REFUSAL)

    writer, canonical, _absolute = resolve_output_target(sink.writer, output_path)
    content = _render(result, fmt)
    report = (await writer(canonical, content)).text

    return cast(
        ToolOutput[T | RedirectedOutput],
        _receipt(report, canonical, fmt, content, result, sink.inline_chars),
    )


# The two bases repeat one field and one delegating method because a slotted
# dataclass cannot be mixed into another one, their instance layouts conflict,
# and what they actually share is `redirect_output`, a function both call.
@dataclass(slots=True, frozen=True)
class RedirectingTool[T](AsyncTool[T | RedirectedOutput], ABC):
    """An async tool whose result may be written out instead of returned.

    Parameterised on the payload the tool computes; the receipt joins it in
    the base's own type, so ``__call__`` still overrides compatibly and every
    caller is told in the signature that either can come back.
    """

    sink: OutputSink | None = field(default=None, kw_only=True)

    async def redirect(
        self, result: ToolOutput[T], output_path: str | None
    ) -> ToolOutput[T | RedirectedOutput]:
        """Write *result* to *output_path*, or return it unchanged."""
        return await redirect_output(result, output_path, self.sink)


@dataclass(slots=True, frozen=True)
class RedirectingPathTool[T](AsyncPathTool[T | RedirectedOutput], ABC):
    """A workspace path tool whose result may be written out instead of returned."""

    sink: OutputSink | None = field(default=None, kw_only=True)

    async def redirect(
        self, result: ToolOutput[T], output_path: str | None
    ) -> ToolOutput[T | RedirectedOutput]:
        """Write *result* to *output_path*, or return it unchanged."""
        return await redirect_output(result, output_path, self.sink)
