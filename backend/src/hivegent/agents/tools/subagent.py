"""Subagent-oriented agent tool registrations."""

from dataclasses import dataclass
from typing import Annotated, Literal

from pydantic import Field
from pydantic_ai import FunctionToolset, RunContext
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider

from ...config import settings
from ...prompts import EXPLORE_INSTRUCTIONS, join_instructions
from ..app import user_agent
from ..common import ExploreTaskArg, UserDeps
from .conversation import conversation_toolset
from .explore import explore_toolset
from .web import web_toolset

__all__ = [
    "explore",
    "subagent_toolset",
]

subagent_toolset: FunctionToolset[UserDeps] = FunctionToolset()

ExploreScopeArg = Annotated[
    Literal["documents", "conversations", "web"],
    Field(
        description=(
            "What to explore: `documents` for the document collection, "
            "`conversations` for past chat history, `web` for web research."
        ),
    ),
]


def _subagent_model(deps: UserDeps) -> OpenAIChatModel:
    """Build the model used for subagent calls."""
    llm = deps.llm
    if llm:
        model = settings.llm.small_model or llm.model
        api_key = llm.api_key
        base_url = llm.base_url
    else:
        model = settings.llm.small_model or settings.llm.model
        api_key = settings.llm.api_key
        base_url = settings.llm.base_url or None

    return OpenAIChatModel(
        model,
        provider=OpenAIProvider(
            api_key=api_key,
            base_url=base_url,
        ),
    )


@dataclass(frozen=True, slots=True)
class _ScopeConfig:
    toolset: FunctionToolset[UserDeps]
    instructions: str | None = None


_SCOPES: dict[str, _ScopeConfig] = {
    "documents": _ScopeConfig(explore_toolset, join_instructions([EXPLORE_INSTRUCTIONS])),
    "conversations": _ScopeConfig(conversation_toolset),
    "web": _ScopeConfig(web_toolset),
}


@subagent_toolset.tool
async def explore(
    ctx: RunContext[UserDeps],
    task: ExploreTaskArg,
    scope: ExploreScopeArg = "documents",
) -> str:
    """Explore using a lightweight model.

    Delegates to a subagent that can search and read within the chosen scope.
    Returns a summary of findings. Use this for broad exploration tasks
    like surveying available documents, finding patterns across files,
    reviewing past conversations, or researching topics on the web.
    """
    config = _SCOPES[scope]
    result = await user_agent.run(
        task,
        model=_subagent_model(ctx.deps),
        deps=ctx.deps,
        toolsets=[config.toolset],
        instructions=config.instructions,
        usage=ctx.usage,
    )
    return result.output
