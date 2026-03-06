"""Pydantic AI agent definitions, toolsets, and UserDeps."""

from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from pydantic_ai import Agent, FilteredToolset, FunctionToolset, RunContext
from pydantic_ai.models.openai import OpenAIResponsesModel
from pydantic_ai.providers.openai import OpenAIProvider
from pydantic_ai.toolsets import AbstractToolset

from .chunks import load_document_metadata, rechunk_document
from .config import settings
from .memory import save_memory as _save_memory
from .messages import list_conversations as _list_conversations
from .prompts import EXPLORE_INSTRUCTIONS
from .retrieval import apply_search_tool, mark_dirty
from .store import Casebase
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
    ListChunksTool,
    ListDocumentsTool,
    WebFetch,
    WebSearch,
    WriteDocumentTool,
)
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
# Helpers
# ---------------------------------------------------------------------------


def _workspace_dir(deps: UserDeps) -> Path:
    return deps.store.workspace_dir(settings.data_dir)


# ---------------------------------------------------------------------------
# Explore toolset
# ---------------------------------------------------------------------------

explore_toolset: FunctionToolset[UserDeps] = FunctionToolset()


@explore_toolset.tool(description=ListDocumentsTool.__call__.__doc__)
def list_documents(
    ctx: RunContext[UserDeps],
    subdir: str | None = None,
    max_depth: int | None = None,
) -> list[DocumentSummary]:
    tool = ListDocumentsTool(
        path=_workspace_dir(ctx.deps),
        extension="",
    )
    results = tool(subdir=subdir, max_depth=max_depth)
    doc_filter = ctx.deps.document_filter
    if doc_filter:
        results = [r for r in results if doc_filter(r.filename)]
    return results


@explore_toolset.tool(description=GlobDocumentsTool.__call__.__doc__)
def glob_documents(
    ctx: RunContext[UserDeps],
    pattern: str,
) -> list[str]:
    tool = GlobDocumentsTool(
        path=_workspace_dir(ctx.deps),
        extension="",
    )
    results = tool(pattern)
    doc_filter = ctx.deps.document_filter
    if doc_filter:
        results = [r for r in results if doc_filter(r)]
    return results


@explore_toolset.tool(description=GrepTool.__call__.__doc__)
async def grep(
    ctx: RunContext[UserDeps],
    pattern: str,
    glob: str | None = None,
    context_lines: int = 0,
) -> list[GrepMatch]:
    tool = GrepTool(path=_workspace_dir(ctx.deps))
    matches = await tool(
        pattern,
        glob=glob,
        context_lines=context_lines,
    )
    doc_filter = ctx.deps.document_filter
    if doc_filter:
        matches = [m for m in matches if doc_filter(m.filename)]
    return matches


@explore_toolset.tool
def semantic_search(
    ctx: RunContext[UserDeps],
    query: str,
    type: Literal["dense", "sparse", "hybrid"] = "hybrid",
    top_k: int = 5,
) -> list[RetrievedChunk]:
    """Search chunks using semantic similarity or keyword matching.

    Searches across your personal documents and all group casebases
    you have access to.  Results include a ``store_key`` indicating the
    source (e.g. ``"user:alice"`` or ``"group:engineering"``).

    Use ``"dense"`` for conceptual queries where exact keywords
    may not appear.  Use ``"sparse"`` for queries with specific terms that
    should appear verbatim.  Use ``"hybrid"`` to combine both approaches.

    Args:
        query: Natural language search query.
        type: ``"dense"`` for vector embeddings, ``"sparse"`` for BM25/FTS,
            ``"hybrid"`` for combined.
        top_k: Maximum results to return.
    """
    return apply_search_tool(
        ctx.deps.all_stores,
        type,
        query,
        top_k,
        filter_for_store=ctx.deps.filter_for_store,
    )


@explore_toolset.tool(description=GetDocumentLinesTool.__call__.__doc__)
def get_document_lines(
    ctx: RunContext[UserDeps],
    filename: str,
    start: int = 1,
    end: int | None = None,
) -> DocumentRange | None:
    doc_filter = ctx.deps.document_filter
    if doc_filter and not doc_filter(filename):
        return None
    tool = GetDocumentLinesTool(path=_workspace_dir(ctx.deps))
    return tool(filename, start, end)


@explore_toolset.tool(description=GetDocumentTool.__call__.__doc__)
def get_document(ctx: RunContext[UserDeps], filename: str) -> str | None:
    doc_filter = ctx.deps.document_filter
    if doc_filter and not doc_filter(filename):
        return None
    tool = GetDocumentTool(path=_workspace_dir(ctx.deps))
    return tool(filename)


@explore_toolset.tool(description=ListChunksTool.__call__.__doc__)
def list_chunks(
    ctx: RunContext[UserDeps],
    filename: str,
) -> list[ChunkSummary] | None:
    metadata_dir = ctx.deps.store.metadata_dir(settings.data_dir)

    def _loader(fn: str) -> Sequence[ChunkSummary] | None:
        meta = load_document_metadata(metadata_dir, fn)
        if not meta:
            return None
        return [
            ChunkSummary(
                token_count=c.token_count,
                start_index=c.start_index,
                end_index=c.end_index,
            )
            for c in meta.chunks
        ]

    doc_filter = ctx.deps.document_filter
    if doc_filter and not doc_filter(filename):
        return None
    tool = ListChunksTool(loader=_loader)
    return tool(filename)


@explore_toolset.tool(description=GetChunkTool.__call__.__doc__)
def get_chunk(
    ctx: RunContext[UserDeps],
    filename: str,
    chunk_index: int,
) -> str | None:
    metadata_dir = ctx.deps.store.metadata_dir(settings.data_dir)

    def _loader(fn: str, idx: int) -> str | None:
        meta = load_document_metadata(metadata_dir, fn)
        if not meta:
            return None
        if 0 <= idx < len(meta.chunks):
            return meta.chunks[idx].text
        return None

    doc_filter = ctx.deps.document_filter
    if doc_filter and not doc_filter(filename):
        return None
    tool = GetChunkTool(loader=_loader)
    return tool(filename, chunk_index)


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
async def explore_documents(ctx: RunContext[UserDeps], task: str) -> str:
    """Explore the document collection using a lightweight model.

    Delegates to a subagent that can list, search, and read documents.
    Returns a summary of findings. Use this for broad exploration tasks
    like surveying available documents, finding patterns across files,
    or answering questions that require checking multiple sources.

    Args:
        task: Natural language description of what to explore or find.
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
async def explore_conversations(ctx: RunContext[UserDeps], task: str) -> str:
    """Explore past conversations using a lightweight model.

    Delegates to a subagent that can list and query conversation history.
    Returns a summary of findings.

    Args:
        task: Natural language description of what to find in conversations.
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
async def explore_web(ctx: RunContext[UserDeps], task: str) -> str:
    """Research a topic on the web using a lightweight model.

    Delegates to a subagent that can search and fetch web pages.
    Returns a summary of findings.

    Args:
        task: Natural language description of what to research on the web.
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
    requires_approval=True, description=EditDocumentTool.__call__.__doc__
)
async def edit_document(
    ctx: RunContext[UserDeps],
    filename: str,
    old_string: str,
    new_string: str,
) -> str:
    store = ctx.deps.store

    async def _on_write(fn: str) -> None:
        await rechunk_document(store, fn)
        mark_dirty(store)

    doc_filter = ctx.deps.document_filter
    if doc_filter and not doc_filter(filename):
        return f"Error: '{filename}' is not accessible."
    tool = EditDocumentTool(
        path=_workspace_dir(ctx.deps),
        on_write=_on_write,
    )
    return await tool(filename, old_string, new_string)


@write_toolset.tool(
    requires_approval=True, description=WriteDocumentTool.__call__.__doc__
)
async def write_document(
    ctx: RunContext[UserDeps],
    filename: str,
    content: str,
    mode: Literal["prepend", "append", "replace"] = "replace",
) -> str:
    store = ctx.deps.store

    async def _on_write(fn: str) -> None:
        await rechunk_document(store, fn)
        mark_dirty(store)

    doc_filter = ctx.deps.document_filter
    if doc_filter and not doc_filter(filename):
        return f"Error: '{filename}' is not accessible."
    tool = WriteDocumentTool(
        path=_workspace_dir(ctx.deps),
        extension="",
        on_write=_on_write,
    )
    return await tool(filename, content, mode)


# ---------------------------------------------------------------------------
# Memory toolset
# ---------------------------------------------------------------------------

memory_toolset: FunctionToolset[UserDeps] = FunctionToolset()


@memory_toolset.tool
def save_memory(ctx: RunContext[UserDeps], content: str) -> str:
    """Save information to persistent memory that is preserved across conversations.

    Overwrites the entire memory, so always include previously saved information
    you want to retain.

    Args:
        content: The full markdown content for the memory file.
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
    filter: str,
    filename: str,
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

    Args:
        filter: A jq filter expression.
        filename: The conversation file to query (e.g. ``"abc123.json"``).
    """
    tool = JqTool(
        path=ctx.deps.store.conversations_dir(settings.data_dir),
    )
    return await tool(filter, filename)


# ---------------------------------------------------------------------------
# Web toolset
# ---------------------------------------------------------------------------

web_toolset: FunctionToolset[UserDeps] = FunctionToolset()


@web_toolset.tool(description=WebSearch.__call__.__doc__)
def web_search(
    ctx: RunContext[UserDeps],
    query: str,
    max_results: int = 5,
) -> list[dict[str, str]]:
    tool = WebSearch()
    return tool(query, max_results)


@web_toolset.tool(description=WebFetch.__call__.__doc__)
async def web_fetch(
    ctx: RunContext[UserDeps],
    url: str,
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
