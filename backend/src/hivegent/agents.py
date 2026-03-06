"""Pydantic AI agent definitions, toolsets, and UserDeps."""

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Annotated

from pydantic import Field
from pydantic_ai import Agent, FilteredToolset, FunctionToolset, RunContext
from pydantic_ai.models.openai import OpenAIResponsesModel
from pydantic_ai.providers.openai import OpenAIProvider
from pydantic_ai.toolsets import AbstractToolset

from .config import settings
from .memory import save_memory as _save_memory
from .messages import list_conversations as _list_conversations
from .prompts import EXPLORE_INSTRUCTIONS
from .store import Casebase
from .tool_runtime import (
    edit_document_text,
    get_document_chunk,
    get_document_lines as get_document_lines_for_store,
    get_document_text,
    glob_documents as glob_documents_for_store,
    grep_documents,
    list_document_chunks,
    list_document_summaries,
    semantic_search_documents,
    write_document_text,
)
from .tools import (
    DocumentRange,
    DocumentSummary,
    EditDocumentTool,
    GetChunkTool,
    GetDocumentLinesTool,
    GetDocumentTool,
    GlobDocumentsTool,
    GrepMatch,
    GrepTool,
    JqTool,
    LanceDBSearchTool,
    ListChunksTool,
    ListDocumentsTool,
    WebFetch,
    WebSearch,
    WriteDocumentTool,
)
from .tools.chunks import ChunkIndexArg
from .tools.documents import (
    DocumentEndLineArg,
    DocumentFilenameArg,
    DocumentMaxDepthArg,
    DocumentStartLineArg,
    DocumentSubdirArg,
    GlobPatternArg,
)
from .tools.grep import ContextLinesArg, GrepGlobArg, GrepPatternArg
from .tools.jq import JqFilenameArg, JqFilterArg
from .tools.mutations import (
    DocumentContentArg,
    EditNewStringArg,
    EditOldStringArg,
    WriteModeArg,
)
from .tools.retrieval import SearchQueryArg, SearchTopKArg, SearchTypeArg
from .tools.typing import tool_description
from .tools.web import WebMaxResultsArg, WebQueryArg, WebUrlArg
from .types import (
    ChunkSummary,
    ConversationSummary,
    DocumentFilter,
    LlmConfig,
    RetrievedChunk,
    ToolInfo,
    ToolsSpec,
)

__all__ = [
    "TOOLSET_GROUPS",
    "UserDeps",
    "base_agent",
    "build_toolsets",
    "collect_tool_info",
    "explore_toolset",
    "user_agent",
]

ExploreTaskArg = Annotated[
    str,
    Field(description="Natural language description of what to explore or find."),
]
MemoryContentArg = Annotated[
    str,
    Field(description="Full markdown content to persist as memory."),
]


# ---------------------------------------------------------------------------
# UserDeps
# ---------------------------------------------------------------------------


@dataclass(slots=True, frozen=True)
class UserDeps:
    """Dependencies for user-specific agent operations."""

    user_id: str
    store: Casebase
    group_stores: tuple[Casebase, ...] = ()
    document_filter: DocumentFilter | None = None
    group_filters: dict[str, DocumentFilter] = field(default_factory=dict)
    llm: LlmConfig | None = None

    @property
    def all_stores(self) -> tuple[Casebase, ...]:
        """All stores the user has access to (personal + group)."""
        return (self.store, *self.group_stores)

    def filter_for_store(self, store: Casebase) -> DocumentFilter | None:
        """Get the applicable DocumentFilter for a specific store.

        Returns the user filter for user stores, the per-group filter
        for group stores (if any), or ``None`` if no filter applies.
        """
        if store.kind == "user":
            return self.document_filter
        return self.group_filters.get(store.id)


# ---------------------------------------------------------------------------
# Agents
# ---------------------------------------------------------------------------

# Only introduce new agents when they need different deps types.
base_agent: Agent[None, str] = Agent()
user_agent: Agent[UserDeps, str] = Agent(deps_type=UserDeps)


# ---------------------------------------------------------------------------
# Explore toolset
# ---------------------------------------------------------------------------

explore_toolset: FunctionToolset[UserDeps] = FunctionToolset()


@explore_toolset.tool(description=tool_description(ListDocumentsTool))
def list_documents(
    ctx: RunContext[UserDeps],
    subdir: DocumentSubdirArg = None,
    max_depth: DocumentMaxDepthArg = None,
) -> list[DocumentSummary]:
    return list_document_summaries(
        ctx.deps.store,
        subdir=subdir,
        max_depth=max_depth,
        document_filter=ctx.deps.document_filter,
    )


@explore_toolset.tool(description=tool_description(GlobDocumentsTool))
def glob_documents(
    ctx: RunContext[UserDeps],
    pattern: GlobPatternArg,
) -> list[str]:
    return glob_documents_for_store(
        ctx.deps.store,
        pattern,
        document_filter=ctx.deps.document_filter,
    )


@explore_toolset.tool(description=tool_description(GrepTool))
async def grep(
    ctx: RunContext[UserDeps],
    pattern: GrepPatternArg,
    glob: GrepGlobArg = None,
    context_lines: ContextLinesArg = 0,
) -> list[GrepMatch]:
    return await grep_documents(
        ctx.deps.store,
        pattern,
        glob=glob,
        context_lines=context_lines,
        document_filter=ctx.deps.document_filter,
    )


@explore_toolset.tool(description=tool_description(LanceDBSearchTool))
def semantic_search(
    ctx: RunContext[UserDeps],
    query: SearchQueryArg,
    type: SearchTypeArg = "hybrid",
    top_k: SearchTopKArg = 5,
) -> list[RetrievedChunk]:
    return semantic_search_documents(
        ctx.deps.store,
        query,
        type=type,
        top_k=top_k,
        group_stores=ctx.deps.group_stores,
        filter_for_store=ctx.deps.filter_for_store,
    )


@explore_toolset.tool(description=tool_description(GetDocumentLinesTool))
def get_document_lines(
    ctx: RunContext[UserDeps],
    filename: DocumentFilenameArg,
    start: DocumentStartLineArg = 1,
    end: DocumentEndLineArg = None,
) -> DocumentRange | None:
    return get_document_lines_for_store(
        ctx.deps.store,
        filename,
        start=start,
        end=end,
        document_filter=ctx.deps.document_filter,
    )


@explore_toolset.tool(description=tool_description(GetDocumentTool))
def get_document(
    ctx: RunContext[UserDeps], filename: DocumentFilenameArg
) -> str | None:
    return get_document_text(
        ctx.deps.store,
        filename,
        document_filter=ctx.deps.document_filter,
    )


@explore_toolset.tool(description=tool_description(ListChunksTool))
def list_chunks(
    ctx: RunContext[UserDeps],
    filename: DocumentFilenameArg,
) -> list[ChunkSummary] | None:
    return list_document_chunks(
        ctx.deps.store,
        filename,
        document_filter=ctx.deps.document_filter,
    )


@explore_toolset.tool(description=tool_description(GetChunkTool))
def get_chunk(
    ctx: RunContext[UserDeps],
    filename: DocumentFilenameArg,
    chunk_index: ChunkIndexArg,
) -> str | None:
    return get_document_chunk(
        ctx.deps.store,
        filename,
        chunk_index,
        document_filter=ctx.deps.document_filter,
    )


# ---------------------------------------------------------------------------
# Subagent toolset — one subagent per read-only toolset
# ---------------------------------------------------------------------------

subagent_toolset: FunctionToolset[UserDeps] = FunctionToolset()


def _subagent_model(
    deps: UserDeps,
) -> OpenAIResponsesModel:
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
    ctx: RunContext[UserDeps], task: ExploreTaskArg
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
    ctx: RunContext[UserDeps], task: ExploreTaskArg
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
async def explore_web(ctx: RunContext[UserDeps], task: ExploreTaskArg) -> str:
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


# ---------------------------------------------------------------------------
# Write toolset
# ---------------------------------------------------------------------------

write_toolset: FunctionToolset[UserDeps] = FunctionToolset()


@write_toolset.tool(
    requires_approval=True,
    description=tool_description(EditDocumentTool),
)
async def edit_document(
    ctx: RunContext[UserDeps],
    filename: DocumentFilenameArg,
    old_string: EditOldStringArg,
    new_string: EditNewStringArg,
) -> str:
    return await edit_document_text(
        ctx.deps.store,
        filename,
        old_string,
        new_string,
        document_filter=ctx.deps.document_filter,
    )


@write_toolset.tool(
    requires_approval=True,
    description=tool_description(WriteDocumentTool),
)
async def write_document(
    ctx: RunContext[UserDeps],
    filename: DocumentFilenameArg,
    content: DocumentContentArg,
    mode: WriteModeArg = "replace",
) -> str:
    return await write_document_text(
        ctx.deps.store,
        filename,
        content,
        mode=mode,
        document_filter=ctx.deps.document_filter,
    )


# ---------------------------------------------------------------------------
# Memory toolset
# ---------------------------------------------------------------------------

memory_toolset: FunctionToolset[UserDeps] = FunctionToolset()


@memory_toolset.tool
def save_memory(ctx: RunContext[UserDeps], content: MemoryContentArg) -> str:
    """Save information to persistent memory that is preserved across conversations.

    Overwrites the entire memory, so always include previously saved information
    you want to retain.
    """
    _save_memory(ctx.deps.user_id, content)
    return "Memory saved successfully."


# ---------------------------------------------------------------------------
# Conversation toolset
# ---------------------------------------------------------------------------

conversation_toolset: FunctionToolset[UserDeps] = FunctionToolset()


@conversation_toolset.tool
def list_conversations_tool(
    ctx: RunContext[UserDeps],
) -> list[ConversationSummary]:
    """List past conversations with titles, dates, and message counts.

    Returns summaries sorted by most recent first.
    """
    return _list_conversations(ctx.deps.store.id)


@conversation_toolset.tool
async def query_conversations(
    ctx: RunContext[UserDeps],
    filter: JqFilterArg,
    filename: JqFilenameArg,
) -> str:
    """Run a jq filter on a conversation JSON file.

    Each conversation file has this schema::

        {
            "title": str,
            "created_at": str (ISO datetime),
            "updated_at": str (ISO datetime),
            "messages": [
                {
                    "kind": "request" | "response",
                    "parts": [{"part_kind": "user-prompt", "content": str}, ...]
                },
                ...
            ]
        }

    Example filters:

    - ``.title`` — get the conversation title.
    - ``.messages[].parts[] | select(.content | test("deadline"))``
      — search message content for "deadline".

    """
    tool = JqTool(
        path=ctx.deps.store.conversations_dir(settings.data_dir),
    )
    return await tool(filter, filename)


# ---------------------------------------------------------------------------
# Web toolset
# ---------------------------------------------------------------------------

web_toolset: FunctionToolset[UserDeps] = FunctionToolset()


@web_toolset.tool(description=tool_description(WebSearch))
def web_search(
    ctx: RunContext[UserDeps],
    query: WebQueryArg,
    max_results: WebMaxResultsArg = 5,
) -> list[dict[str, str]]:
    tool = WebSearch()
    return tool(query, max_results)


@web_toolset.tool(description=tool_description(WebFetch))
async def web_fetch(
    ctx: RunContext[UserDeps],
    url: WebUrlArg,
) -> str:
    tool = WebFetch()
    return await tool(url)


# ---------------------------------------------------------------------------
# Toolset groups & helpers
# ---------------------------------------------------------------------------

TOOLSET_GROUPS: dict[str, FunctionToolset[UserDeps]] = {
    "explore": explore_toolset,
    "subagent": subagent_toolset,
    "write": write_toolset,
    "memory": memory_toolset,
    "web": web_toolset,
    "conversation": conversation_toolset,
}


def build_toolsets[T](
    toolsets: Sequence[FunctionToolset[T]],
    tools_spec: ToolsSpec,
    extra: Sequence[AbstractToolset[T]] = (),
) -> Sequence[AbstractToolset[T]]:
    """Apply disabled-tool filtering and append extra toolsets.

    Args:
        toolsets: Built-in agent toolsets.
        tools_spec: Combined tool configuration from the chat request.
        extra: Additional toolsets to append (e.g. MCP servers).

    Returns:
        Sequence of toolsets ready to pass to the agent.
    """
    result: list[AbstractToolset[T]] = []

    if tools_spec.disabled_tools:
        disabled = frozenset(tools_spec.disabled_tools)
        result.extend(
            FilteredToolset(
                wrapped=ts,
                filter_func=lambda _ctx, td, _disabled=disabled: (
                    td.name not in _disabled
                ),
            )
            for ts in toolsets
        )
    else:
        result.extend(toolsets)

    result.extend(extra)
    return result


def collect_tool_info[T](
    toolset_groups: dict[str, FunctionToolset[T]],
) -> list[ToolInfo]:
    """Collect metadata from all registered toolset groups.

    Args:
        toolset_groups: Mapping of group name to toolset.

    Returns:
        Flat list of tool info entries.
    """
    result: list[ToolInfo] = []
    for group, toolset in toolset_groups.items():
        for name, tool in toolset.tools.items():
            result.append(
                ToolInfo(name=name, description=tool.description or "", group=group)
            )
    return result
