"""Cross-cutting run-loop safeguards composed onto the agents.

Four capabilities that guard a run without belonging to any one feature:

* :class:`IncompleteToolCallGuard` fails a turn whose response the token
  limit cut off mid tool call, before the half-written call is dispatched.

* :class:`ToolOutputLimit` bounds the plain text a tool return sends the
  model, head+tail clamping anything over the budget while leaving the
  structured ``DataChunk`` (and any multimodal content) untouched, so a
  runaway return — a foreign MCP tool that self-caps nothing, or a built-in
  tool rendering an outsized window — cannot dominate the context and
  re-cost every later request.
* :class:`PromptImageLimit` holds one outgoing request to the number of
  images the serving gateway accepts, swapping the older surplus for a note,
  so a batch of parallel reads that each obey the cap cannot overflow it
  together.
* :class:`IterationLimitWarner` injects a user-turn nudge as the run nears
  its request budget, so the model wraps up before the hard
  :class:`~pydantic_ai.exceptions.UsageLimitExceeded` abort rather than
  being cut off mid-task.

All four hook the pydantic-ai agent loop, so they touch only the agent
path; the framework-neutral tools and their FastMCP adapter are unaffected.
:class:`IncompleteToolCallGuard` and :class:`PromptImageLimit` ride on the
agents themselves (see ``agents/app.py``), because a truncated call and a
request the gateway will reject are hazards on every run, not only the
mode-composed chat one; the other two are composed per chat run.

The two that reshape the conversation do it on the wire and nowhere else,
through ``wrap_model_request``: the messages handed to the handler are what
the model sees, while the graph keeps its own and records those, so a note
never enters the stored conversation and never accumulates across requests.
``before_model_request`` is not that seam — ``request_context.messages`` is
the run's history, so editing or replacing it there rewrites the tree the
conversation is replayed from and exported out of.  Nor are the messages the
place to edit in place: the parts and their content lists are shared with
that tree, so a reshape rebuilds what it touches.

:class:`ToolOutputLimit` is the deliberate exception — it reduces the tool
return itself, which is recorded in place of the original, so there the
reduction is what persists.
"""

from collections.abc import Sequence
from dataclasses import dataclass, replace
from typing import Any

from pydantic_ai import BinaryContent
from pydantic_ai.capabilities import AbstractCapability, WrapModelRequestHandler
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

from ..tools.formatting import truncate_middle
from .common import UserDeps

__all__ = [
    "IncompleteToolCallGuard",
    "IterationLimitWarner",
    "PromptImageLimit",
    "ToolOutputLimit",
]


def _image_list(part: ModelRequestPart) -> list[Any] | None:
    """The content list of a part that can carry images, else ``None``."""
    carries = isinstance(part, UserPromptPart | ToolReturnPart)

    return part.content if carries and isinstance(part.content, list) else None


def _images(messages: Sequence[ModelMessage]) -> list[BinaryContent]:
    """Every image the request carries, oldest first — what the gateway counts."""
    return [
        item
        for message in messages
        if isinstance(message, ModelRequest)
        for part in message.parts
        if (content := _image_list(part)) is not None
        for item in content
        if isinstance(item, BinaryContent) and item.is_image
    ]


def _swap_images(
    part: ModelRequestPart, displaced: set[int], note: str
) -> ModelRequestPart:
    """*part* with each of its *displaced* images swapped for *note*.

    Rebuilt rather than edited: the content list is shared with the persisted
    message tree, which an in-place swap would strip the image from too.
    """
    content = _image_list(part)
    if content is None:
        return part

    return replace(
        part, content=[note if id(item) in displaced else item for item in content]
    )


def _within_image_cap(
    messages: list[ModelMessage], max_images: int | None
) -> list[ModelMessage]:
    """*messages* with all but the newest *max_images* images swapped for a note.

    Returned unchanged when the gateway counts nothing (``None``) or the
    request already fits, so the ordinary request is never rebuilt.  Displaced
    images are keyed by identity, since two that compare equal are still two.
    """
    images = _images(messages)
    if max_images is None or len(images) <= max_images:
        return messages

    note = (
        f"[image not shown: a model request carries at most {max_images} "
        "image(s), and newer ones displaced this. Read the document again "
        "if you still need to see it.]"
    )
    displaced = {id(image) for image in images[: len(images) - max_images]}
    kept: list[ModelMessage] = []
    for message in messages:
        if isinstance(message, ModelRequest):
            message = replace(
                message,
                parts=[_swap_images(part, displaced, note) for part in message.parts],
            )

        kept.append(message)

    return kept


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
            raise IncompleteToolCall(
                f"Model token limit ({max_tokens or 'provider default'}) exceeded "
                "while generating a tool call, resulting in incomplete arguments."
            )

        return response


@dataclass(slots=True)
class ToolOutputLimit(AbstractCapability[UserDeps]):
    """Bound the plain text a tool return sends the model, keeping structured data.

    Applies to every tool uniformly.  A tool return carries two channels: the
    LLM-facing text in ``ToolReturn.return_value`` and the structured payload
    the frontend reads in ``ToolReturn.metadata`` (a ``DataChunk``).  Only the
    text channel is touched — a ``str`` return value over ``max_chars`` is
    head+tail truncated and the return rebuilt with ``replace``, so the
    ``DataChunk`` rides through untouched.  A non-``str`` return value (a
    ``[text, BinaryContent]`` list, or a bare structured object) is left as-is,
    so no binary or structured payload is ever stringified and lost.

    This bounds *rendered* text, which the built-in tools' own content caps do
    not (line numbers and markup are added on top), so ``max_chars`` is a
    coarse backstop set above those caps: a considered read is not re-clamped,
    but a runaway return — foreign or built-in — cannot dominate the context.
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
        """Clamp an oversized plain-text tool return, keeping structured data."""
        if isinstance(result, str):
            text = result
        elif isinstance(result, ToolReturn) and isinstance(result.return_value, str):
            text = result.return_value
        else:
            return result

        if len(text) <= self.max_chars:
            return result

        truncated = truncate_middle(text, self.max_chars)

        return (
            truncated
            if isinstance(result, str)
            else replace(result, return_value=truncated)
        )


@dataclass(slots=True)
class PromptImageLimit(AbstractCapability[Any]):
    """Hold one model request to the images the serving gateway accepts.

    The cap belongs to the serving gateway (vLLM's ``--limit-mm-per-prompt``),
    which counts the images of a whole request and rejects it entirely, so an
    overflow is a failed turn rather than a refusal anything can act on.  What
    it counts is the request, so that is what is counted here, once, where the
    outgoing messages are in hand: the images a step's parallel reads
    attached, the turn's own attachments, and the ones already in the replayed
    history — none of which any per-call budget can see.

    All but the newest ``max_images`` are swapped for a note, on the wire
    alone.  Trimming rather than refusing the call keeps the
    rendering the run already paid for and spends no extra round trip, and the
    note is what keeps it honest: the model is told an image it asked for is
    not in front of it, so it reads the document again instead of answering
    from a picture it cannot see.  ``None`` is a gateway that counts nothing.
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
        messages = _within_image_cap(request_context.messages, self.max_images)
        if messages is not request_context.messages:
            request_context = replace(request_context, messages=messages)

        return await handler(request_context)


@dataclass(slots=True)
class IterationLimitWarner(AbstractCapability[UserDeps]):
    """Nudge the model to finish as the run nears its request budget.

    Once the run has used ``threshold`` of ``max_requests`` model requests
    (shared across the main agent and its subagents, which run on the same
    usage accumulator), a short user-turn note is appended to the outgoing
    request stating how many requests remain.  The note rides only the
    per-request message copy pydantic-ai builds, so it steers the model
    without ever entering the persisted conversation, and it is re-derived
    each request rather than accumulating.
    """

    max_requests: int
    threshold: float = 0.75

    async def before_model_request(
        self,
        ctx: RunContext[UserDeps],
        request_context: ModelRequestContext,
    ) -> ModelRequestContext:
        """Append a wrap-up note once the request budget is nearly spent."""
        used = ctx.usage.requests

        if used < self.threshold * self.max_requests:
            return request_context

        remaining = max(0, self.max_requests - used)
        note = (
            f"[run-limit-warning] You have used {used} of {self.max_requests} model "
            f"requests for this turn ({remaining} remaining). Wrap up: give your "
            f"best answer now and avoid unnecessary tool calls."
        )
        request_context.messages = [
            *request_context.messages,
            ModelRequest(parts=[UserPromptPart(content=note)]),
        ]

        return request_context
