"""A plain language model call, offered to a sandboxed program alone.

The program-side counterpart of a sub-agent in ``pydantic-ai-harness``'s
dynamic workflows: one tool-free model run per call, awaited inside the
program, so classifying or extracting from many items is one loop rather than
one model turn per item.  The completion itself is injected, which keeps this
module free of any model, settings, or run.  Its budget per turn is the
injected completion's and the errors a program meets are the sandbox
boundary's (:class:`~hivegent.tools.monty.HostCalls`), so this tool only completes.
"""

from collections.abc import Awaitable, Callable
from typing import Annotated, Literal

from exar.tools import effect
from exar.tools.batch import tool_return
from pydantic import Field
from pydantic_ai import ToolReturn

from ..shared.l10n import Localized
from .base import ToolFunc

__all__ = [
    "COMPLETE_INSTRUCTIONS",
    "Completion",
    "CompletionPromptArg",
    "CompletionReasoning",
    "CompletionReasoningArg",
    "complete",
]

COMPLETE_INSTRUCTIONS: Localized[str] = Localized(
    en="""
Use `complete` to classify, extract from, or summarize many items in a loop or with `asyncio.gather`. It has no tools and no documents, so pass each item's text in `prompt`, and every call counts against a fixed budget per turn. It answers without reasoning, so pass `reasoning="low"`, `"medium"`, or `"high"` only where an item needs thought, since that is slower.
""",
    de="""
Nutze `complete`, um viele Elemente in einer Schleife oder mit `asyncio.gather` zu klassifizieren, auszuwerten oder zusammenzufassen. Die Funktion hat keine Tools und keine Dokumente, gib den Text jedes Elements also in `prompt` mit, und jeder Aufruf zählt gegen ein festes Budget pro Runde. Sie antwortet ohne Nachdenken, gib `reasoning="low"`, `"medium"` oder `"high"` also nur mit, wo ein Element Überlegung braucht, da das langsamer ist.
""",
)
"""Added only where ``complete`` is live, since the prose names it."""


type CompletionReasoning = Literal["low", "medium", "high"]
"""The effort a completion reasons with."""

type Completion = Callable[[str, CompletionReasoning | None], Awaitable[str]]
"""Complete one prompt, reasoning at the effort if any, and return the text."""

CompletionPromptArg = Annotated[
    str,
    Field(
        description=(
            "The whole request: the instruction and every text it is about, "
            "since the model sees nothing else."
        ),
    ),
]

CompletionReasoningArg = Annotated[
    CompletionReasoning | None,
    Field(
        description=(
            "How hard the model thinks before answering, none by default, "
            "since reasoning is slower and only pays off for hard items."
        ),
    ),
]


def complete(completion: Completion) -> ToolFunc:
    """The tool that completes prompts with a language model through *completion*."""

    @effect("read")
    async def complete(
        prompt: CompletionPromptArg, reasoning: CompletionReasoningArg = None
    ) -> ToolReturn[str]:
        """Answer `prompt` with a language model and return its text.

        The model has no tools, no documents, and no memory of earlier calls.
        It reasons only at the `reasoning` effort given.
        Every call counts against a fixed budget per turn.
        """
        return tool_return(await completion(prompt, reasoning))

    return complete
