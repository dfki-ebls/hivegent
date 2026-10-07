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
  file, while leaving the structured ``DataChunk`` (and the binary items of a
  multimodal return) untouched, so a runaway return cannot dominate the
  context and re-cost every later request, and none of it is out of reach.
  The cut itself is :func:`~hivegent.tools.formatting.truncate_middle`, which
  the MCP adapter applies to its returns too.
* :class:`PromptImageLimit` holds one outgoing request to the number of
  images the serving gateway accepts, swapping the older surplus for a note,
  so a batch of parallel reads that each obey the cap cannot overflow it
  together.
* :class:`IterationLimitWarner` injects a user-turn nudge as the run nears
  its request budget, so the model wraps up before the hard
  :class:`~pydantic_ai.exceptions.UsageLimitExceeded` abort rather than
  being cut off mid-task.

All five hook the pydantic-ai agent loop, so they touch only the agent
path; the framework-neutral tools and their FastMCP adapter are unaffected.
All but :class:`IterationLimitWarner`, which is composed per chat run, ride on
the agents themselves (see ``agents/app.py``), because a localized tool
result, a truncated call, a request the gateway will reject, and a runaway
return are hazards on every run, not only the mode-composed chat one.  The
chat run's :class:`~hivegent.agents.approval.ApprovalNotes` declares that it
wraps the spill, so a note is appended to the bounded return and never cut
off.

The two that reshape the conversation do it on the wire and nowhere else,
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

import asyncio
import hashlib
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from functools import partial
from typing import Any, TypeGuard

from pydantic_ai import BinaryContent, ImageUrl
from pydantic_ai.capabilities import (
    AbstractCapability,
    RawToolArgs,
    ValidatedToolArgs,
    WrapModelRequestHandler,
    WrapToolExecuteHandler,
    WrapToolValidateHandler,
)
from pydantic_ai.exceptions import IncompleteToolCall
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelRequestPart,
    ModelResponse,
    ToolCallPart,
    ToolReturn,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.models import ModelRequestContext
from pydantic_ai.tools import RunContext, ToolDefinition
from pydantic_core import to_json

from ..humanize import format_bytes, pluralize
from ..l10n import DEFAULT_LANGUAGE, Language, Localized, current_language, use_language
from ..tmp import save_result
from ..tools.base import SearchPath, ToolRetry
from ..tools.formatting import truncate_middle
from ..tools.pydantic_ai import unwrap_tool_output
from .common import UserDeps

__all__ = [
    "EnglishToolCalls",
    "IncompleteToolCallGuard",
    "IterationLimitWarner",
    "PromptImageLimit",
    "ToolOutputSpill",
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


def _run_limit_warning(used: int, max_requests: int, remaining: int) -> Localized[str]:
    return Localized(
        en=(
            f"[run-limit-warning] You have used {used} of {max_requests} "
            f"model requests for this turn ({remaining} remaining). Wrap up: "
            f"give your best answer now and avoid unnecessary tool calls."
        ),
        de=(
            f"[run-limit-warning] Du hast {used} von {max_requests} "
            f"Modellanfragen für diese Runde verbraucht ({remaining} übrig). "
            "Komm zum Schluss: Gib jetzt deine beste Antwort und vermeide "
            "unnötige Tool-Aufrufe."
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

    if isinstance(part, UserPromptPart) and not isinstance(part.content, str):
        return part.content

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
    this raises its ``IncompleteToolCall`` up front.  Either way
    :func:`~hivegent.llm.is_context_overflow` classifies it and the frontend
    compacts the conversation and retries the turn — the right remedy while
    ``llm.max_tokens`` is unset on the main tier, which is its documented
    default.

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
    root: SearchPath, call: ToolCallPart, text: str, data: Any
) -> Callable[[str], str] | None:
    """Save the whole result into *root*, returning the note naming it, or ``None``.

    The structured data is saved where there is any, since it is whole where
    the text may be a rendering of part of it, and the text otherwise.  It is
    serialized and sized on a worker thread.  ``None`` for a result the folder
    cannot hold, which then keeps only what the preview shows.
    """
    structured = data is not None
    name = _file_name(call, "json" if structured else "txt")

    def serialize() -> tuple[str, int]:
        if not structured:
            return text, len(text.encode())

        encoded = to_json(data)

        return encoded.decode(), len(encoded)

    try:
        content, size = await asyncio.to_thread(serialize)
        path = await save_result(root.path, name, content)
    except ToolRetry:
        return None

    return partial(_saved_note, path, size, data)


@dataclass(slots=True)
class ToolOutputSpill(AbstractCapability[UserDeps]):
    """Bound the plain text a tool return sends the model, saving the whole of a larger one.

    Applies to every tool uniformly, MCP ones included.  A tool return carries
    two channels: the LLM-facing text in ``ToolReturn.return_value`` and the
    structured payload the frontend reads in ``ToolReturn.metadata`` (a
    ``DataChunk``).  A text over ``max_chars`` is saved whole under the
    conversation's ``/tmp/.tool-results/`` (the structured data as ``.json`` where
    there is any and it is the result, the text as ``.txt`` otherwise) and
    replaced by its head and tail around a note naming the file, so the model
    reads on from the file rather than calling the tool again.  Only the text
    channel is touched, so the ``DataChunk`` rides through untouched.  A run
    without a ``/tmp`` gets the same head and tail with a plain note.

    The text is a ``str`` return value or the leading ``str`` of a content
    list such as ``[text, BinaryContent]``, whose other items are kept as they
    are, so no binary or structured payload is ever stringified and lost.

    The decision is the harness's rather than the model's: a tool argument that
    asked the model to route a result into a file was misread time and again.
    """

    max_chars: int

    async def after_tool_execute(
        self,
        ctx: RunContext[UserDeps],
        *,
        call: ToolCallPart,
        tool_def: ToolDefinition,
        args: dict[str, Any],
        result: Any,
    ) -> Any:
        """Save an oversized plain text tool return and show a preview of it."""
        output = unwrap_tool_output(result)
        text = output.text

        if text is None or len(text) <= self.max_chars:
            return result

        root = ctx.deps.tmp
        data = None if output.display_only else output.data
        note = None if root is None else await _save(root, call, text, data)
        preview = truncate_middle(text, self.max_chars, note)
        bounded = preview if output.rest is None else [preview, *output.rest]

        return (
            replace(result, return_value=bounded)
            if isinstance(result, ToolReturn)
            else bounded
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


@dataclass(slots=True)
class IterationLimitWarner(AbstractCapability[UserDeps]):
    """Nudge the model to finish as the run nears its request budget.

    The budget is the run's own ``ctx.usage_limits``, so the warning names the
    limit that will actually stop the run, and a run without a request limit
    is never warned.  Once the run has used ``threshold`` of it (counted on the
    usage the main agent shares with its subagents), a short user-turn note is
    appended to the outgoing request stating how many requests remain.  It
    rides the wire alone, so it steers the model without entering the
    persisted conversation and is re-derived each request rather than
    accumulating: appended to the run's own messages it would be recorded as a
    user turn nobody sent, replayed on every later turn, and joined by one more
    note per warned request.  It is written in the run's language, since an
    English user turn would pull the final answer into English.
    """

    threshold: float = 0.75

    async def wrap_model_request(
        self,
        ctx: RunContext[UserDeps],
        *,
        request_context: ModelRequestContext,
        handler: WrapModelRequestHandler,
    ) -> ModelResponse:
        """Append a wrap-up note once the request budget is nearly spent."""
        used = ctx.usage.requests
        limit = ctx.usage_limits.request_limit if ctx.usage_limits else None

        if limit is not None and used >= self.threshold * limit:
            note = _run_limit_warning(used, limit, max(0, limit - used))[
                _run_language(ctx)
            ]
            request_context = replace(
                request_context,
                messages=[
                    *request_context.messages,
                    ModelRequest(parts=[UserPromptPart(content=note)]),
                ],
            )

        return await handler(request_context)
