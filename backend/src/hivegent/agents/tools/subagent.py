"""Subagent-oriented agent tool registrations."""

from pydantic_ai import FunctionToolset, RunContext
from pydantic_ai.models.openai import OpenAIResponsesModel
from pydantic_ai.providers.openai import OpenAIProvider

from ...config import settings
from ...prompts import EXPLORE_INSTRUCTIONS
from ..app import user_agent
from ..common import ExploreTaskArg, UserDeps
from .conversation import conversation_toolset
from .explore import explore_toolset
from .web import web_toolset

__all__ = [
    "explore_conversations",
    "explore_documents",
    "explore_web",
    "subagent_toolset",
]

subagent_toolset: FunctionToolset[UserDeps] = FunctionToolset()


def _subagent_model(deps: UserDeps) -> OpenAIResponsesModel:
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

    return OpenAIResponsesModel(
        model,
        provider=OpenAIProvider(
            api_key=api_key,
            base_url=base_url,
        ),
    )


@subagent_toolset.tool
async def explore_documents(
    ctx: RunContext[UserDeps],
    task: ExploreTaskArg,
) -> str:
    """Explore the document collection using a lightweight model.

    Delegates to a subagent that can list, search, and read documents.
    Returns a summary of findings. Use this for broad exploration tasks
    like surveying available documents, finding patterns across files,
    or answering questions that require checking multiple sources.
    """
    result = await user_agent.run(
        task,
        model=_subagent_model(ctx.deps),
        deps=ctx.deps,
        toolsets=[explore_toolset],
        instructions=EXPLORE_INSTRUCTIONS,
        usage=ctx.usage,
    )
    return result.output


@subagent_toolset.tool
async def explore_conversations(
    ctx: RunContext[UserDeps],
    task: ExploreTaskArg,
) -> str:
    """Explore past conversations using a lightweight model.

    Delegates to a subagent that can list and query conversation history.
    Returns a summary of findings.
    """
    result = await user_agent.run(
        task,
        model=_subagent_model(ctx.deps),
        deps=ctx.deps,
        toolsets=[conversation_toolset],
        usage=ctx.usage,
    )
    return result.output


@subagent_toolset.tool
async def explore_web(
    ctx: RunContext[UserDeps],
    task: ExploreTaskArg,
) -> str:
    """Research a topic on the web using a lightweight model.

    Delegates to a subagent that can search and fetch web pages.
    Returns a summary of findings.
    """
    result = await user_agent.run(
        task,
        model=_subagent_model(ctx.deps),
        deps=ctx.deps,
        toolsets=[web_toolset],
        usage=ctx.usage,
    )
    return result.output
