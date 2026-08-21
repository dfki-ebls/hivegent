"""Cross-cutting run-loop safeguards composed onto the agents.

Three capabilities that guard a run without belonging to any one feature:

* :class:`IncompleteToolCallGuard` fails a turn whose response the token
  limit cut off mid tool call, before the half-written call is dispatched.

* :class:`ToolOutputLimit` bounds the plain text a tool return sends the
  model, head+tail clamping anything over the budget while leaving the
  structured ``DataChunk`` (and any multimodal content) untouched, so a
  runaway return — a foreign MCP tool that self-caps nothing, or a built-in
  tool rendering an outsized window — cannot dominate the context and
  re-cost every later request.
* :class:`IterationLimitWarner` injects a user-turn nudge as the run nears
  its request budget, so the model wraps up before the hard
  :class:`~pydantic_ai.exceptions.UsageLimitExceeded` abort rather than
  being cut off mid-task.

All three hook the pydantic-ai agent loop, so they touch only the agent
path; the framework-neutral tools and their FastMCP adapter are unaffected.
:class:`IncompleteToolCallGuard` rides on the agents themselves (see
``agents/app.py``) because a truncated call is a hazard on every run, not
only the mode-composed chat one; the other two are composed per chat run.

Neither of the editing two touches the persisted message tree, only the
per-request message copy or the tool return: pydantic-ai passes a fresh
``message_history`` copy to ``before_model_request``, and a reduced return is
recorded in place of the original, so the reduction is what persists.
"""

from dataclasses import dataclass, replace
from typing import Any

from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.exceptions import IncompleteToolCall
from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    ToolCallPart,
    ToolReturn,
    UserPromptPart,
)
from pydantic_ai.models import ModelRequestContext
from pydantic_ai.tools import RunContext, ToolDefinition

from .common import UserDeps

__all__ = [
    "IncompleteToolCallGuard",
    "IterationLimitWarner",
    "ToolOutputLimit",
]


@dataclass(slots=True)
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


def _truncate_middle(text: str, max_chars: int) -> str:
    """Clamp *text* to about *max_chars*, keeping the head and tail.

    Errors land at the tail of build/test output and schemas at the head,
    so both ends are kept and the elided middle is marked.  The caller only
    invokes this once ``text`` already exceeds ``max_chars``.
    """
    head = max_chars * 2 // 3
    tail = max_chars - head

    return (
        f"{text[:head]}\n\n"
        f"[tool output truncated: showing first {head:,} + "
        f"last {tail:,} of {len(text):,} characters]\n\n"
        f"{text[-tail:]}"
    )


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

        truncated = _truncate_middle(text, self.max_chars)

        return (
            truncated
            if isinstance(result, str)
            else replace(result, return_value=truncated)
        )


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
