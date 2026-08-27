"""Conversation summarization for compaction and subagent recovery.

A summary is asked for as one more turn of the conversation being summarized:
:data:`COMPACT_PROMPT` is appended to that conversation's own message list and
run under the prefix its turns already ran under, so everything ahead of the
prompt is the block the provider just cached and only the prompt is prefilled.
Re-rendering the same history as a flat transcript under different instructions
would share nothing with that cache and pay a full prefill of a nearly-full
window, which is what this path exists to avoid.

The catch is that the continuation is the *larger* request, and summarization
is reached exactly when a conversation has stopped fitting.  Room is made by
dropping messages from the **tail**: the head is the cached prefix, so cutting
there throws away the very thing being reused, while cutting the tail leaves a
shorter prefix of the same block.  Where to cut comes from the conversation
itself -- ``ModelResponse.usage`` records what each request carried, and the
largest one the endpoint accepted is a floor on its context window -- so no
tokenizer, model card, or configured window enters here.  A cut lands only
where every tool call has been answered, since a provider rejects a history
ending on an open one; that is also what makes this usable for a subagent that
crashed mid-call.

An estimate can still be wrong, since the provider counts a prompt its own way
and a turn's report describes the request before it, so a refusal is not fatal:
each one sheds the summary's actual output cap plus a small prompt reserve and
asks again.
"""

import logging
from collections.abc import Sequence
from typing import NamedTuple

from pydantic_ai.exceptions import ModelHTTPError, UnexpectedModelBehavior
from pydantic_ai.messages import (
    ModelMessage,
    ModelResponse,
    RetryPromptPart,
    ToolCallPart,
    ToolReturnPart,
)
from pydantic_ai.settings import ModelSettings
from pydantic_ai.usage import UsageLimits

from ..llm import SUMMARY_MAX_TOKENS, is_context_overflow, summary_model_settings
from .app import user_agent
from .common import RunPrefix

__all__ = ["COMPACT_PROMPT", "summarize_conversation"]

logger = logging.getLogger(__name__)

COMPACT_PROMPT = """\
Summarize the conversation as a handover note for continuing the work in a fresh context.

Use this exact structure, keeping every heading even where a section is empty:

## Goal
[What the user is trying to accomplish, in one or two sentences.]

## Constraints and decisions
- [Preferences, requirements, and choices made, each with its reason; otherwise "(none)"]

## Progress
### Done
- [Finished work and established findings; otherwise "(none)"]
### In progress
- [Work underway or partially applied; otherwise "(none)"]
### Blocked
- [Blockers, failures, and unknowns; otherwise "(none)"]

## Documents
- [Full workspace path: why it matters; otherwise "(none)"]

## Next steps
1. [The immediate concrete action; otherwise "(none)"]

Keep every section to terse bullets rather than prose, and reproduce workspace paths, quoted passages, names, and figures exactly as they appeared.
Answer from the conversation above alone, and do not call any tools.
Return only the summary, with no commentary around it."""
"""The request, appended to the live conversation as its last user turn.

Bounded by structure rather than by a word count, which would make the model
drop sections instead of tightening them.  The tools stay declared, so the
closing sentences are the only thing holding the model back from calling one:
withdrawing them, or sending ``tool_choice='none'``, would rewrite the block
that sits ahead of the messages and throw away the prefix this path exists for.
"""

# Keep the chat turn's tools declared so the provider prefix stays identical,
# but reject every call before it can execute.
_COMPACT_LIMITS = UsageLimits(request_limit=1, tool_calls_limit=0)

_COMPACT_PROMPT_RESERVE_TOKENS = 1024
"""How much a refused attempt sheds before asking again: what the request needs
on top of its actual completion cap, covering the prompt and counting drift.

The thousand over the completion cap covers the prompt itself and the drift
between the count one request reported and what the provider measures for the
next.  Shedding by what the request needs rather than by a share of the history
is what keeps the summary whole: the tail is the most recent work, and a
conversation that overflowed did so by one turn, not by half of itself.
"""

_MAX_ATTEMPTS = 3
"""How many prefixes to offer before giving up.

An overflow is refused before the endpoint prefills anything, so a spare
attempt is cheap, but a fourth would mean the reported counts bear no relation
to what the provider measures and no amount of shedding will land.
"""


class _Cut(NamedTuple):
    """A prefix of a conversation, and what it measured when last sent."""

    keep: int
    size: int


def _cut_points(messages: Sequence[ModelMessage]) -> list[_Cut]:
    """The prefixes of *messages* the compact prompt can be appended to.

    A prefix is continuable only where every tool call it makes has been
    answered, since a provider rejects a history that ends on an open one.  Its
    size is the count ``ModelResponse.usage`` already carries: input plus
    output is exactly what the following request sent, so a prefix ending in
    tool returns is measured by the response before them and understates by
    those returns, which a refusal then corrects.  Ascending; a prefix no turn
    ever reported a size for is not offered.

    Neither is one ending on a fresh user prompt: it measures the same as the
    turn before it while keeping a message the provider never counted, so it
    sheds nothing.  Leaving those out is also what makes the longest prefix
    here the longest one the endpoint has actually served -- the whole history
    when the last turn succeeded, and everything but the refused prompt when it
    did not.
    """
    cuts: list[_Cut] = []
    open_calls: set[str] = set()
    size = 0

    for i, msg in enumerate(messages):
        answered = False
        for part in msg.parts:
            if isinstance(part, ToolCallPart):
                open_calls.add(part.tool_call_id)
            elif isinstance(part, ToolReturnPart | RetryPromptPart):
                open_calls.discard(part.tool_call_id)
                answered = True

        served = answered or isinstance(msg, ModelResponse)
        if isinstance(msg, ModelResponse) and msg.usage.total_tokens:
            size = msg.usage.total_tokens

        if size and served and not open_calls:
            cuts.append(_Cut(i + 1, size))

    return cuts


def _plan(
    messages: Sequence[ModelMessage],
    max_output_tokens: int = SUMMARY_MAX_TOKENS,
) -> list[int]:
    """How many leading messages to keep, per attempt, longest first.

    The first attempt keeps everything the endpoint has served, because it is
    the cheaper judge of whether that still fits: an overflow is refused before
    anything is prefilled, while a prefix trimmed on suspicion spends a full
    request to answer from less of the conversation than was available.  Only a
    refusal sheds, and it sheds what the request needs rather than a share of
    the history, so the summary walks back one turn at a time.

    A conversation nothing ever reported a size for gets that first attempt and
    no more: with no measurement, there is no next prefix to name.
    """
    cuts = _cut_points(messages) or [_Cut(len(messages), 0)]
    plan = [cuts[-1].keep]
    budget = cuts[-1].size
    reserve = max_output_tokens + _COMPACT_PROMPT_RESERVE_TOKENS

    for _ in range(_MAX_ATTEMPTS - 1):
        smaller = next(
            (c for c in reversed(cuts) if c.size + reserve <= budget),
            None,
        )
        if smaller is None or smaller.keep >= plan[-1]:
            break

        plan.append(smaller.keep)
        budget = smaller.size

    return plan


async def _ask(
    messages: Sequence[ModelMessage],
    run: RunPrefix,
    model_settings: ModelSettings,
) -> str:
    """Run the compact prompt as the next turn of *messages*."""
    result = await user_agent.run(
        COMPACT_PROMPT,
        message_history=list(messages),
        deps=run.deps,
        model=run.model,
        model_settings=model_settings,
        capabilities=run.capabilities,
        instructions=run.instructions,
        usage_limits=_COMPACT_LIMITS,
    )

    return result.output.strip()


async def summarize_conversation(
    messages: Sequence[ModelMessage], run: RunPrefix
) -> str:
    """Summarize *messages* as the next turn of the conversation they are.

    Args:
        messages: The conversation to summarize, trimmed to fit as needed.
        run: The prompt prefix these messages already ran under, model
            included, which the request reproduces so the provider's cache
            answers for it.

    Returns:
        A handover note for continuing the work in a fresh context.

    Raises:
        ModelHTTPError | UnexpectedModelBehavior: If the model rejects the
            request for a reason other than context overflow, or still has no
            room once there is nothing left to shed.
    """
    model_settings = summary_model_settings(run.llm)
    *retries, final = _plan(
        messages, model_settings.get("max_tokens", SUMMARY_MAX_TOKENS)
    )

    for cut in retries:
        try:
            return await _ask(messages[:cut], run, model_settings)

        except (ModelHTTPError, UnexpectedModelBehavior) as exc:
            if not is_context_overflow(exc):
                raise

            logger.info(
                "Compaction overflowed at %d of %d messages, shedding",
                cut,
                len(messages),
            )

    return await _ask(messages[:final], run, model_settings)
