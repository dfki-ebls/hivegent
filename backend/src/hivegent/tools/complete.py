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
from dataclasses import dataclass
from typing import Annotated, ClassVar, override

from pydantic import Field

from ..l10n import Localized
from ..prompts import SANDBOX_COMPLETE_INSTRUCTIONS
from .base import AsyncTool, ToolOutput

__all__ = ["CompleteTool", "Completion", "CompletionPromptArg"]

type Completion = Callable[[str], Awaitable[str]]
"""Complete one prompt and return the text."""

CompletionPromptArg = Annotated[
    str,
    Field(
        description=(
            "The whole request: the instruction and every text it is about, "
            "since the model sees nothing else."
        ),
    ),
]


@dataclass(slots=True, frozen=True)
class CompleteTool(AsyncTool[str]):
    """Complete prompts with a language model."""

    injectable: ClassVar[bool] = True
    registered: ClassVar[bool] = False
    sandbox_instructions: ClassVar[Localized[str] | None] = SANDBOX_COMPLETE_INSTRUCTIONS

    completion: Completion

    @override
    async def __call__(self, prompt: CompletionPromptArg) -> ToolOutput[str]:
        """Answer `prompt` with a language model and return its text.

        The model has no tools, no documents, and no memory of earlier calls.
        Every call counts against a fixed budget per turn.
        """
        return ToolOutput(data=await self.completion(prompt))
