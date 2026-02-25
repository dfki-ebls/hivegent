"""RAG agent with document retrieval tools."""

import logging
from dataclasses import dataclass, field
from typing import Literal

from pydantic_ai import Agent, FunctionToolset, RunContext
from pydantic_ai.models.openai import OpenAIResponsesModel
from pydantic_ai.providers.openai import OpenAIProvider

from .config import settings
from .prompts import EXPLORE_INSTRUCTIONS
from .store import Casebase
from .tool_factory import ToolFactory
from .types import (
    ChunkSummary,
    ConversationSummary,
    DocumentFilter,
    DocumentRange,
    DocumentSummary,
    GrepMatch,
    LlmConfig,
    RetrievedChunk,
)

__all__ = [
    "UserDeps",
    "base_agent",
    "explore_agent",
    "explore_toolset",
    "rag_toolset",
    "user_agent",
    "write_toolset",
]

logger = logging.getLogger(__name__)


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

    @property
    def tool_factory(self) -> ToolFactory:
        """Create a ToolFactory from these dependencies."""
        return ToolFactory(
            store=self.store,
            document_filter=self.document_filter,
            group_stores=self.group_stores,
            group_filters=self.group_filters,
        )


base_agent: Agent[None, str] = Agent()
user_agent: Agent[UserDeps, str] = Agent(deps_type=UserDeps)
explore_agent: Agent[UserDeps, str] = Agent(deps_type=UserDeps)

# --- Explore toolset (lightweight exploration tools) ---

explore_toolset: FunctionToolset[UserDeps] = FunctionToolset()


@explore_toolset.tool
def list_documents(
    ctx: RunContext[UserDeps],
    subdir: str | None = None,
    max_depth: int | None = None,
) -> list[DocumentSummary]:
    """List all available documents with their sizes in bytes.

    Args:
        subdir: Only include documents under this subdirectory.
        max_depth: Maximum nesting depth relative to *subdir* (or root).
    """
    return ctx.deps.tool_factory.list_documents(subdir=subdir, max_depth=max_depth)


@explore_toolset.tool
def glob_documents(
    ctx: RunContext[UserDeps],
    pattern: str,
) -> list[str]:
    """Find documents matching a glob pattern.

    Args:
        pattern: Glob pattern to match (e.g., "*.md", "notes/*.txt", "**/*.py").
    """
    return ctx.deps.tool_factory.glob_documents(pattern)


@explore_toolset.tool
def grep(
    ctx: RunContext[UserDeps],
    pattern: str,
    glob: str | None = None,
    context_lines: int = 0,
    include_content: bool = True,
) -> list[GrepMatch]:
    """Search documents for a pattern.

    Uses smart case matching: case-insensitive unless the pattern contains
    uppercase letters.

    Args:
        pattern: Text or regex pattern to search for.
        glob: Only search files matching this pattern (e.g., "*.md", "notes/*").
        context_lines: Number of lines to show before and after each match.
        include_content: Whether to include the matching line content.
    """
    return ctx.deps.tool_factory.grep(
        pattern,
        glob=glob,
        context_lines=context_lines,
        include_content=include_content,
    )


@explore_toolset.tool
def semantic_search(
    ctx: RunContext[UserDeps],
    query: str,
    type: Literal["dense", "sparse"] = "dense",
    top_k: int = 5,
) -> list[RetrievedChunk]:
    """Search chunks using semantic similarity or keyword matching.

    Searches across your personal documents and all group casebases
    you have access to.  Results include a ``store_key`` indicating the
    source (e.g. ``"user:alice"`` or ``"group:engineering"``).

    Use ``"dense"`` (default) for conceptual queries where exact keywords
    may not appear.  Use ``"sparse"`` for queries with specific terms that
    should appear verbatim.

    Args:
        query: Natural language search query.
        type: ``"dense"`` for vector embeddings, ``"sparse"`` for BM25/FTS.
        top_k: Maximum results to return.
    """
    factory = ctx.deps.tool_factory
    search = factory.dense_search if type == "dense" else factory.sparse_search
    return search(query, top_k)


@explore_toolset.tool
def get_document_lines(
    ctx: RunContext[UserDeps],
    filename: str,
    start: int = 1,
    end: int | None = None,
) -> DocumentRange | None:
    """Get a range of lines from a document.

    Args:
        filename: The relative document path (e.g. "report.md" or "projects/report.md").
        start: First line to include (1-indexed, default: 1).
        end: Last line to include (1-indexed, default: end of file).
    """
    return ctx.deps.tool_factory.get_document_lines(filename, start, end)


@explore_toolset.tool
def list_conversations(
    ctx: RunContext[UserDeps],
) -> list[ConversationSummary]:
    """List past conversations with titles, dates, and message counts.

    Returns summaries sorted by most recent first.
    """
    return ctx.deps.tool_factory.list_conversations()


@explore_toolset.tool
def query_conversations(
    ctx: RunContext[UserDeps],
    filter: str,
    filename: str | None = None,
) -> str:
    """Run a jq filter on conversation JSON files.

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

    When no ``filename`` is given, all conversations are collected into
    an array with an ``"id"`` field injected from each filename stem.

    Example filters:

    - ``.[].title`` — list all conversation titles.
    - ``[.[] | select(.title | test("budget"; "i"))]`` — find
      conversations whose title mentions "budget".
    - ``.messages[].parts[] | select(.content | test("deadline"))``
      — search message content for "deadline" (single-file mode).

    Args:
        filter: A jq filter expression.
        filename: Query a specific conversation file (e.g. ``"abc123.json"``).
            If omitted, all conversations are queried.
    """
    return ctx.deps.tool_factory.query_conversations(filter, filename)


# --- RAG toolset (heavier retrieval tools + explore delegation) ---

rag_toolset: FunctionToolset[UserDeps] = FunctionToolset()


@rag_toolset.tool
def get_document(ctx: RunContext[UserDeps], filename: str) -> str | None:
    """Get the full content of a specific document.

    Args:
        filename: The relative path to retrieve (e.g. "report.md" or "projects/report.md").
    """
    return ctx.deps.tool_factory.get_document(filename)


@rag_toolset.tool
def list_chunks(
    ctx: RunContext[UserDeps],
    filename: str,
) -> list[ChunkSummary] | None:
    """List chunk metadata for a document.

    Args:
        filename: The relative document path (e.g. "report.md" or "projects/report.md").
    """
    return ctx.deps.tool_factory.list_chunks(filename)


@rag_toolset.tool
def get_chunk(
    ctx: RunContext[UserDeps],
    filename: str,
    chunk_index: int,
) -> str | None:
    """Get the text content of a specific chunk.

    Args:
        filename: The relative document path (e.g. "report.md" or "projects/report.md").
        chunk_index: The index of the chunk to retrieve.
    """
    return ctx.deps.tool_factory.get_chunk(filename, chunk_index)


@rag_toolset.tool
async def explore_documents(ctx: RunContext[UserDeps], task: str) -> str:
    """Explore the document collection using a lightweight model.

    Delegates to a subagent that can list, search, and read documents.
    Returns a summary of findings. Use this for broad exploration tasks
    like surveying available documents, finding patterns across files,
    or answering questions that require checking multiple sources.

    Args:
        task: Natural language description of what to explore or find.
    """
    llm = ctx.deps.llm
    if llm:
        # Resolved config: user overrides already merged with server defaults.
        model = settings.llm.small_model or llm.model
        api_key = llm.api_key
        base_url = llm.base_url
    else:
        # No user config (e.g. MCP context) – use server defaults.
        model = settings.llm.small_model or settings.llm.model
        api_key = settings.llm.api_key
        base_url = settings.llm.base_url or None

    result = await explore_agent.run(
        task,
        model=OpenAIResponsesModel(
            model,
            provider=OpenAIProvider(
                api_key=api_key,
                base_url=base_url,
            ),
        ),
        deps=ctx.deps,
        toolsets=[explore_toolset],
        instructions=EXPLORE_INSTRUCTIONS,
        usage=ctx.usage,
    )
    return result.output


# --- Write toolset (document mutation tools, require approval) ---

write_toolset: FunctionToolset[UserDeps] = FunctionToolset()


@write_toolset.tool(requires_approval=True)
def edit_document(
    ctx: RunContext[UserDeps],
    filename: str,
    old_string: str,
    new_string: str,
) -> str:
    """Edit a document by replacing an exact string with new text.

    Use this to make precise, surgical changes. The old_string must
    appear exactly once in the file to prevent ambiguous edits.

    Args:
        filename: The relative document path (e.g. "notes/todo.md").
        old_string: The exact text to replace. Must appear exactly once.
        new_string: The replacement text.
    """
    return ctx.deps.tool_factory.edit_document(filename, old_string, new_string)


@write_toolset.tool(requires_approval=True)
def write_document(
    ctx: RunContext[UserDeps],
    filename: str,
    content: str,
    mode: Literal["prepend", "append", "replace"] = "replace",
) -> str:
    """Write content to a document.

    Args:
        filename: The relative document path (e.g. "notes/todo.md").
        content: The text content to write.
        mode: ``"replace"`` overwrites (creates if absent), ``"append"``
            adds to the end, ``"prepend"`` adds to the start.
    """
    return ctx.deps.tool_factory.write_document(filename, content, mode)
