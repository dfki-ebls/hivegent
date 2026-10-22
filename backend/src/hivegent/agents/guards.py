"""Cross-cutting run-loop safeguards composed onto the agents.

Five capabilities that guard a run without belonging to any one feature:

* :class:`EnglishToolCalls` validates and executes every tool call in the
  default language, so tool results and retries stay English whatever the
  interface language of the request the run serves.
* :class:`IncompleteToolCallGuard` fails a turn whose response the token
  limit cut off mid tool call, before the half-written call is dispatched.

* :class:`ToolOutputSpill` bounds the plain text a tool return sends the
  model, saving a larger one whole under the conversation's
  ``/tmp/.tool-results/`` and showing its head and tail around a note naming the
  file, and keeps the structured ``DataChunk`` only where it is neither saved
  there nor too large for the frontend, so a runaway return cannot dominate the
  context and re-cost every later request, and none of it is out of reach.
  The cut itself is :func:`~hivegent.tools.formatting.truncate_middle`, which
  the MCP adapter applies to its returns too.
* :class:`PromptImageLimit` holds one outgoing request to the number of
  images the serving gateway accepts, swapping the older surplus for a note,
  so a batch of parallel reads that each obey the cap cannot overflow it
  together.
* :class:`LimitNotes` tells the model in a tool result that the run nears its
  request budget or a tool its retries, so it wraps up or changes course
  before the hard abort rather than being cut off mid-task.

All five hook the pydantic-ai agent loop, so they touch only the agent
path; the framework-neutral tools and their FastMCP adapter are unaffected.
All ride on the agents themselves (see ``agents/app.py``), because a
localized tool result, a truncated call, a request the gateway will reject,
a runaway return, and a spent budget are hazards on every run, not only the
mode-composed chat one.  :class:`LimitNotes` and the chat run's
:class:`~hivegent.agents.approval.ApprovalNotes` declare that they wrap the
spill, so a note is appended to the bounded return and never cut off.

The one that reshapes the conversation does it on the wire and nowhere else,
through ``wrap_model_request``: the messages handed to the handler are what
the model sees, while the graph keeps its own and records those, so a note
never enters the stored conversation and never accumulates across requests.
``before_model_request`` is not that seam, as ``request_context.messages`` is
the run's history, so editing or replacing it there rewrites the tree the
conversation is replayed from and exported out of.  Nor are the messages the
place to edit in place: the parts and their content lists are shared with
that tree, so a reshape rebuilds what it touches.

:class:`ToolOutputSpill` is the deliberate exception: it reduces the tool
return itself, which is recorded in place of the original, so there the
reduction is what persists.
"""

import hashlib
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field, replace
from functools import partial
from typing import Any, NoReturn, Self, TypeGuard

from pydantic import ValidationError
from pydantic_ai import BinaryContent, ImageUrl, ModelRetry
from pydantic_ai.capabilities import (
    AbstractCapability,
    CapabilityOrdering,
    RawToolArgs,
    ValidatedToolArgs,
    WrapModelRequestHandler,
    WrapToolExecuteHandler,
    WrapToolValidateHandler,
)
from pydantic_ai.exceptions import IncompleteToolCall, ToolRetryError
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelRequestPart,
    ModelResponse,
    RetryPromptPart,
    ToolCallPart,
    ToolReturn,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.models import ModelRequestContext
from pydantic_ai.tools import RunContext, ToolDefinition
from pydantic_ai.ui.vercel_ai.response_types import DataChunk

from ..db.conversations import prompt_items
from ..humanize import format_bytes, pluralize
from ..l10n import DEFAULT_LANGUAGE, Language, Localized, current_language, use_language
from ..prompts import request_limit_note, retry_limit_note
from ..tmp import save_result
from ..tools.base import SearchPath, ToolRetry
from ..tools.formatting import truncate_middle
from ..tools.pydantic_ai import encoded_data, unwrap_tool_output
from .common import UserDeps

__all__ = [
    "EnglishToolCalls",
    "IncompleteToolCallGuard",
    "LimitNotes",
    "PromptImageLimit",
    "ToolOutputSpill",
    "with_note",
]


def _incomplete_tool_call(max_tokens: int | None) -> Localized[str]:
    return Localized(
        en=(
            f"Model token limit ({max_tokens or 'provider default'}) exceeded "
            "while generating a tool call, resulting in incomplete arguments."
        ),
        de=(
            f"Token-Limit des Modells ({max_tokens or 'Standard des Anbieters'}) "
            "beim Erzeugen eines Tool-Aufrufs überschritten, die Argumente sind "
            "daher unvollständig."
        ),
    )


def _image_not_shown(max_images: int) -> Localized[str]:
    return Localized(
        en=(
            f"[image not shown: a model request carries at most {max_images} "
            f"{pluralize(max_images, 'image')}, and newer ones displaced this. "
            "Read the document again if you still need to see it.]"
        ),
        de=(
            f"[Bild nicht angezeigt: Eine Modellanfrage enthält höchstens "
            f"{max_images} {pluralize(max_images, 'Bild', 'Bilder')}, und neuere "
            "haben dieses verdrängt. Lies das Dokument erneut, wenn du es noch "
            "sehen musst.]"
        ),
    )


def _run_language(ctx: RunContext[Any]) -> Language:
    """The run's own language where its deps carry one, else the request's.

    The guards ride on every agent, including ``title_agent``, whose runs have
    no :class:`UserDeps` to state a language.
    """
    if isinstance(ctx.deps, UserDeps):
        return ctx.deps.language

    return current_language()


def _image_list(part: ModelRequestPart) -> Sequence[Any]:
    """Inspect the same content shapes the provider sends as images."""
    if isinstance(part, ToolReturnPart):
        return part.content_items()

    if isinstance(part, UserPromptPart):
        return prompt_items(part)

    return ()


def _is_image(item: Any) -> TypeGuard[BinaryContent | ImageUrl]:
    return isinstance(item, ImageUrl) or (
        isinstance(item, BinaryContent) and item.is_image
    )


def _images(messages: Sequence[ModelMessage]) -> list[BinaryContent | ImageUrl]:
    """Every image occurrence in request order."""
    return [
        item
        for message in messages
        if isinstance(message, ModelRequest)
        for part in message.parts
        for item in _image_list(part)
        if _is_image(item)
    ]


def _within_image_cap(
    messages: list[ModelMessage], max_images: int | None, language: Language
) -> list[ModelMessage]:
    """Replace older image occurrences without mutating shared history.

    The note standing in for an image is written in *language*, since it reads
    like the rest of the turn it replaces part of.
    """
    if max_images is None:
        return messages

    remaining = len(_images(messages)) - max_images

    if remaining <= 0:
        return messages

    note = _image_not_shown(max_images)[language]
    kept: list[ModelMessage] = []

    for message in messages:
        if remaining > 0 and isinstance(message, ModelRequest):
            parts = list(message.parts)

            for index, part in enumerate(parts):
                content = list(_image_list(part))
                changed = False

                for position, item in enumerate(content):
                    if remaining > 0 and _is_image(item):
                        content[position] = note
                        remaining -= 1
                        changed = True

                if changed:
                    replacement = (
                        content[0]
                        if isinstance(part, ToolReturnPart)
                        and not isinstance(part.content, list)
                        else content
                    )
                    parts[index] = replace(part, content=replacement)

            message = replace(message, parts=parts)

        kept.append(message)

    return kept


class EnglishToolCalls(AbstractCapability[Any]):
    """Validate and execute every tool call in the default language.

    Tool results and retries are read by the model alone, and the workspace,
    URL policy and subagent code a tool reaches words its receipts and errors
    in the ambient language of the request.  Pinning that language for the
    duration of the call keeps every tool result English, the subagent runs a
    tool starts included.
    """

    async def wrap_tool_validate(
        self,
        ctx: RunContext[Any],
        *,
        call: ToolCallPart,
        tool_def: ToolDefinition,
        args: RawToolArgs,
        handler: WrapToolValidateHandler,
    ) -> ValidatedToolArgs:
        """Validate the arguments in the default language."""
        with use_language(DEFAULT_LANGUAGE):
            return await handler(args)

    async def wrap_tool_execute(
        self,
        ctx: RunContext[Any],
        *,
        call: ToolCallPart,
        tool_def: ToolDefinition,
        args: ValidatedToolArgs,
        handler: WrapToolExecuteHandler,
    ) -> Any:
        """Execute the tool in the default language."""
        with use_language(DEFAULT_LANGUAGE):
            return await handler(args)


class IncompleteToolCallGuard(AbstractCapability[Any]):
    """Fail a turn whose response the token limit cut off mid tool call.

    A response the provider ends with ``finish_reason == "length"`` stops
    wherever the budget ran out.  Cut mid tool call, the arguments are
    whatever fit: usually unparseable JSON, occasionally valid JSON missing
    its tail.  Neither is safe to dispatch — the first sends pydantic-ai into
    a retry that re-submits the same overflowing prompt (and gets truncated
    again, since the prompt itself still fits), the second executes a call
    the model never finished asking for, such as a half-written document
    edit.

    pydantic-ai reaches the same conclusion on its own, but only for
    unparseable arguments and only once the tool's retry budget is spent, so
    this raises its ``IncompleteToolCall`` up front.  Its partial response
    has already streamed, so the turn is not compacted and retried in place
    (:class:`~hivegent.agents.compaction.CompactOnOverflow`), and compacting by
    hand is the remedy while ``llm.max_tokens`` is unset on the main tier,
    which is its documented default.

    Truncated prose is left alone: it is a degraded but usable answer, and
    the aux tier caps its completions on purpose.
    """

    async def after_model_request(
        self,
        ctx: RunContext[Any],
        *,
        request_context: ModelRequestContext,
        response: ModelResponse,
    ) -> ModelResponse:
        """Reject a length-truncated response that carries a tool call."""
        if response.finish_reason == "length" and response.tool_calls:
            max_tokens = (request_context.model_settings or {}).get("max_tokens")
            message = _incomplete_tool_call(max_tokens)[_run_language(ctx)]
            raise IncompleteToolCall(message)

        return response


def _file_name(call: ToolCallPart, suffix: str) -> str:
    """Name a call's saved result after its tool and a short, path safe digest of its id."""
    tool = re.sub(r"[^\w.-]", "_", call.tool_name)
    tag = hashlib.sha256(call.tool_call_id.encode()).hexdigest()[:8]

    return f"{tool}-{tag}.{suffix}"


def _saved_note(path: str, size: int, data: Any, head: str) -> str:
    """Say where the whole result went, how large it is, and how to read on."""
    parts = data if isinstance(data, list | tuple) else (data,)
    count = (
        f" with {len(parts)} {pluralize(len(parts), 'entry', 'entries')}"
        if isinstance(data, list | tuple)
        else ""
    )
    clipped = (
        " The tool had already cut it short of what was asked for, so raise its "
        "limit or narrow the request for the rest."
        if any(getattr(part, "truncated", False) is True for part in parts)
        else ""
    )
    # The text is saved as shown, so the first line the head left out is
    # where a read of the file resumes.
    offset = head.count("\n") + 2 if head else 1
    follow = (
        "Query it with jq or run_python, or search it with grep"
        if data is not None
        else f"Read on with read_document from offset={offset}, or search it with grep or run_python"
    )

    return (
        "[The result is too large to show whole, so its middle is left out here. "
        f"All of it, {format_bytes(size)}{count}, is saved at "
        f"`{path}`.{clipped} {follow} rather than calling the tool again.]"
    )


async def _save(
    root: SearchPath, call: ToolCallPart, text: str, data: Any, encoded: bytes | None
) -> Callable[[str], str] | None:
    """Save the whole result into *root*, returning the note naming it, or ``None``.

    The structured *data* is saved as its *encoded* JSON where that is the
    result, and the text otherwise.  ``None`` for a result the folder cannot
    hold, which then keeps only what the preview shows.
    """
    suffix, content, size, noted = (
        ("txt", text, len(text.encode()), None)
        if encoded is None
        else ("json", encoded.decode(), len(encoded), data)
    )

    try:
        path = await save_result(root.path, _file_name(call, suffix), content)
    except ToolRetry:
        return None

    return partial(_saved_note, path, size, noted)


@dataclass(slots=True)
class ToolOutputSpill(AbstractCapability[UserDeps]):
    """Bound both channels of a tool return, saving the whole of a larger one.

    Applies to every tool uniformly, MCP ones included.  A tool return carries
    two channels: the LLM-facing text in ``ToolReturn.return_value`` and the
    structured payload the frontend reads in ``ToolReturn.metadata`` (a
    ``DataChunk``).  A text over ``max_chars`` is saved whole under the
    conversation's ``/tmp/.tool-results/`` (the structured data as ``.json`` where
    there is any and it is the result, the text as ``.txt`` otherwise) and
    replaced by its head and tail around a note naming the file, so the model
    reads on from the file rather than calling the tool again.  A run without a
    ``/tmp`` gets the same head and tail with a plain note.

    The structured data is kept once: a chunk whose data went to the file is
    dropped, and so is one over ``max_data_bytes``, since the frontend loads
    every chunk of a conversation and the database stores it.  The text then
    stands alone, and names the file where there is one.

    A failed call's retry prompt is clamped to ``max_chars`` the same way, as
    one bound covers every text a tool sends the model, but never saved, since
    a retry is no result to read on from.

    The text is a ``str`` return value or the leading ``str`` of a content
    list such as ``[text, BinaryContent]``, whose other items are kept as they
    are, so no binary or structured payload is ever stringified and lost.

    The decision is the harness's rather than the model's: a tool argument that
    asked the model to route a result into a file was misread time and again.
    """

    max_chars: int
    max_data_bytes: int

    async def wrap_tool_execute(
        self,
        ctx: RunContext[UserDeps],
        *,
        call: ToolCallPart,
        tool_def: ToolDefinition,
        args: ValidatedToolArgs,
        handler: WrapToolExecuteHandler,
    ) -> Any:
        """Bound a failed call's retry prompt, which never reaches :meth:`after_tool_execute`."""
        try:
            return await handler(args)

        except ToolRetryError as error:
            retry = error.tool_retry

            if (
                not isinstance(retry.content, str)
                or len(retry.content) <= self.max_chars
            ):
                raise

            bounded = truncate_middle(retry.content, self.max_chars)

            raise ToolRetryError(replace(retry, content=bounded)) from error

    async def after_tool_execute(
        self,
        ctx: RunContext[UserDeps],
        *,
        call: ToolCallPart,
        tool_def: ToolDefinition,
        args: ValidatedToolArgs,
        result: Any,
    ) -> Any:
        """Save an oversized tool return and bound what the model and the frontend get."""
        output = unwrap_tool_output(result)
        text = output.text
        wrapped = isinstance(result, ToolReturn)
        chunk = result.metadata if wrapped else None
        # The one encoding the stream sends too, inline, since serializing
        # holds the GIL on a worker thread all the same.
        encoded = encoded_data(chunk) if isinstance(chunk, DataChunk) else None
        data_over = encoded is not None and len(encoded) > self.max_data_bytes

        if text is None or len(text) <= self.max_chars:
            return replace(result, metadata=None) if data_over else result

        saved = None if output.display_only else encoded
        root = ctx.deps.tmp
        note = (
            None if root is None else await _save(root, call, text, output.data, saved)
        )
        preview = truncate_middle(text, self.max_chars, note)
        value = preview if output.rest is None else [preview, *output.rest]

        if not wrapped:
            return value

        # The data is kept once: in the file it went to, or in the chunk within its budget.
        kept = not data_over and (saved is None or note is None)

        return replace(
            result, return_value=value, metadata=result.metadata if kept else None
        )


@dataclass(slots=True)
class PromptImageLimit(AbstractCapability[Any]):
    """Hold one model request to the images the serving gateway accepts.

    Replace older images with a note on the wire only.
    The allowance covers history, user attachments, and parallel tool returns.
    ``None`` disables the cap.
    """

    max_images: int | None

    async def wrap_model_request(
        self,
        ctx: RunContext[Any],
        *,
        request_context: ModelRequestContext,
        handler: WrapModelRequestHandler,
    ) -> ModelResponse:
        """Send all but the newest ``max_images`` images as a note."""
        messages = _within_image_cap(
            request_context.messages, self.max_images, _run_language(ctx)
        )
        if messages is not request_context.messages:
            request_context = replace(request_context, messages=messages)

        return await handler(request_context)


def with_note(result: Any, note: str) -> Any:
    """Append *note* to a tool's model-facing return, keeping its metadata.

    A list already is one, a multimodal return's text and attachments, so the
    note joins it rather than nesting it, and anything else becomes its first
    item.  Every noted return is the same shape, and the client shows a list
    item by item.

    Examples:
        >>> with_note("written", "Note.")
        ['written', 'Note.']
        >>> with_note(["text", 1], "Note.")
        ['text', 1, 'Note.']
    """
    if isinstance(result, ToolReturn):
        return replace(result, return_value=with_note(result.return_value, note))

    return [*result, note] if isinstance(result, list) else [result, note]


@dataclass(slots=True)
class LimitNotes(AbstractCapability[Any]):
    """Tell the model in a tool result that a limit of the turn is nearly spent.

    Two limits end a run: its request budget, the run's own
    ``ctx.usage_limits`` counted on the usage the main agent shares with its
    subagents, and a tool's retries, ``ctx.max_retries`` failures in a row.
    Once ``threshold`` of either is spent, a note naming that limit joins what
    the model reads next: a failing call's retry prompt carries its tool's
    note, and the first result or retry after the request budget runs low
    carries the request note, once per run.

    A note rides a tool result rather than a user message of its own, which
    would void the cached prefix mid-turn (see "One budget per turn" in the
    README), and is written in the run's language like the other guard notes.
    """

    threshold: float = 0.75
    _request_noted: bool = field(default=False, init=False)

    async def for_run(self, ctx: RunContext[Any]) -> Self:
        """A fresh instance, so each run reports the request note once."""
        return replace(self)

    def get_ordering(self) -> CapabilityOrdering:
        """Wrap the spill, so a note joins the bounded return and is never cut off."""
        return CapabilityOrdering(wraps=[ToolOutputSpill])

    def _take_request_note(self, ctx: RunContext[Any]) -> str | None:
        """The request note if it is due, marked as reported."""
        limit = ctx.usage_limits.request_limit if ctx.usage_limits else None

        if (
            self._request_noted
            or limit is None
            or ctx.usage.requests < self.threshold * limit
        ):
            return None

        self._request_noted = True

        return request_limit_note(limit)[_run_language(ctx)]

    def _due_notes(self, ctx: RunContext[Any]) -> list[str]:
        """The notes due on the failure of the call *ctx* runs."""
        failures = ctx.retry + 1
        notes: list[str] = []

        if failures >= self.threshold * ctx.max_retries:
            notes.append(
                retry_limit_note(ctx.tool_name or "", failures, ctx.max_retries)[
                    _run_language(ctx)
                ]
            )

        if (note := self._take_request_note(ctx)) is not None:
            notes.append(note)

        return notes

    def _raise_noted(
        self, ctx: RunContext[Any], retry: RetryPromptPart, error: Exception
    ) -> NoReturn:
        """Raise *error* again, as its *retry* prompt with the due notes if any."""
        if not (notes := self._due_notes(ctx)):
            raise error

        text = "\n\n".join([str(ToolRetryError(retry)), *notes])

        raise ToolRetryError(replace(retry, content=text)) from error

    async def on_tool_validate_error(
        self,
        ctx: RunContext[Any],
        *,
        call: ToolCallPart,
        tool_def: ToolDefinition,
        args: RawToolArgs,
        error: ValidationError | ModelRetry,
    ) -> ValidatedToolArgs:
        """Add the due notes to a rejected call's retry prompt."""
        retry = RetryPromptPart.from_error(
            error, tool_name=call.tool_name, tool_call_id=call.tool_call_id
        )
        self._raise_noted(ctx, retry, error)

    async def wrap_tool_execute(
        self,
        ctx: RunContext[Any],
        *,
        call: ToolCallPart,
        tool_def: ToolDefinition,
        args: ValidatedToolArgs,
        handler: WrapToolExecuteHandler,
    ) -> Any:
        """Add the due notes to a failed call's retry prompt."""
        try:
            return await handler(args)

        except ToolRetryError as error:
            self._raise_noted(ctx, error.tool_retry, error)

    async def after_tool_execute(
        self,
        ctx: RunContext[Any],
        *,
        call: ToolCallPart,
        tool_def: ToolDefinition,
        args: ValidatedToolArgs,
        result: Any,
    ) -> Any:
        """Add the request note to the first result after the budget runs low."""
        note = self._take_request_note(ctx)

        return result if note is None else with_note(result, note)
