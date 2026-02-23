"""FastMCP server with OIDCProxy auth and explicit typed tool wrappers."""

import logging
from typing import Literal

from fastmcp import Context, FastMCP
from fastmcp.dependencies import CurrentAccessToken, Depends  # pyright: ignore[reportAttributeAccessIssue]
from fastmcp.server.auth import AccessToken, OIDCProxy
from pydantic_ai.models.openai import OpenAIResponsesModel
from pydantic_ai.providers.openai import OpenAIProvider

from . import tools
from .agent import UserDeps, explore_agent, explore_toolset
from .auth import auth_settings
from .config import settings
from .prompts import EXPLORE_INSTRUCTIONS
from .store import Casebase
from .types import (
    ChunkSummary,
    DocumentRange,
    DocumentSummary,
    GrepMatch,
    RetrievedChunk,
)

__all__ = ["mcp_app"]

logger = logging.getLogger(__name__)


mcp_auth: OIDCProxy | None = None

if not auth_settings.disabled:
    mcp_auth = OIDCProxy(
        config_url=f"{auth_settings.issuer}/.well-known/openid-configuration",
        client_id=settings.mcp.client_id,
        client_secret=settings.mcp.client_secret,
        base_url=settings.mcp.base_url,
    )

mcp_app = FastMCP("SnipScout", auth=mcp_auth)


def _get_mcp_user_id(
    access_token: AccessToken | None = CurrentAccessToken(),
) -> str:
    """Extract user ID from the MCP auth token's ``sub`` claim."""
    if auth_settings.disabled:
        return "localhost"
    if access_token is None:
        raise RuntimeError("No authenticated user in MCP context")
    sub = access_token.claims.get("sub")
    if not isinstance(sub, str) or not sub:
        raise RuntimeError("Token missing 'sub' claim")
    return sub


def _get_mcp_user_store(
    access_token: AccessToken | None = CurrentAccessToken(),
) -> Casebase:
    """Build the user's Casebase from the MCP auth token."""
    return Casebase(kind="user", id=_get_mcp_user_id(access_token))


def _get_mcp_group_stores(
    access_token: AccessToken | None = CurrentAccessToken(),
) -> tuple[Casebase, ...]:
    """Build group Casebases from the MCP auth token's group claims.

    Parses the permission suffix format (``group:read``, ``group:write``,
    or bare ``group``) to extract just the group ID.
    """
    if auth_settings.disabled:
        return ()
    if access_token is None:
        return ()
    raw = access_token.claims.get(settings.groups.groups_claim, [])
    if not isinstance(raw, list):
        return ()
    stores: list[Casebase] = []
    seen: set[str] = set()
    for entry in raw:
        if not isinstance(entry, str) or not entry:
            continue
        # Strip permission suffix (e.g. "engineering:write" -> "engineering")
        group_id = entry.rpartition(":")[0] if ":" in entry else entry
        if group_id in seen:
            continue
        seen.add(group_id)
        try:
            stores.append(Casebase(kind="group", id=group_id))
        except ValueError:
            continue
    return tuple(stores)


@mcp_app.tool()
def list_documents(
    subdir: str | None = None,
    max_depth: int | None = None,
    store: Casebase = Depends(_get_mcp_user_store),
) -> list[DocumentSummary]:
    """List all available documents with their sizes in bytes."""
    return tools.ListDocumentsTool(
        path=store.documents_dir(settings.data_dir),
    )(subdir=subdir, max_depth=max_depth)


@mcp_app.tool()
def get_document(
    filename: str,
    store: Casebase = Depends(_get_mcp_user_store),
) -> str | None:
    """Get the full content of a specific document by relative path."""
    return tools.GetDocumentTool(
        path=store.documents_dir(settings.data_dir),
    )(filename)


@mcp_app.tool()
def get_document_lines(
    filename: str,
    start: int = 1,
    end: int | None = None,
    store: Casebase = Depends(_get_mcp_user_store),
) -> DocumentRange | None:
    """Get a range of lines from a document by relative path."""
    return tools.GetDocumentLinesTool(
        path=store.documents_dir(settings.data_dir),
    )(filename, start, end)


@mcp_app.tool()
def glob_documents(
    pattern: str,
    store: Casebase = Depends(_get_mcp_user_store),
) -> list[str]:
    """Find documents matching a glob pattern."""
    return tools.GlobDocumentsTool(
        path=store.documents_dir(settings.data_dir),
    )(pattern)


@mcp_app.tool()
def grep(
    pattern: str,
    glob: str | None = None,
    context_lines: int = 0,
    include_content: bool = True,
    store: Casebase = Depends(_get_mcp_user_store),
) -> list[GrepMatch]:
    """Search documents for a pattern."""
    return tools.GrepTool(
        path=store.documents_dir(settings.data_dir),
    )(
        pattern,
        glob=glob,
        context_lines=context_lines,
        include_content=include_content,
    )


@mcp_app.tool()
def semantic_search(
    query: str,
    type: Literal["dense", "sparse"] = "dense",
    top_k: int = 5,
    store: Casebase = Depends(_get_mcp_user_store),
    group_stores: tuple[Casebase, ...] = Depends(_get_mcp_group_stores),
) -> list[RetrievedChunk]:
    """Search chunks using semantic similarity or keyword matching.

    Searches across personal documents and all group casebases.
    Use "dense" for vector embeddings (conceptual queries),
    "sparse" for BM25/FTS (keyword queries).
    """
    all_stores = (store, *group_stores)
    return tools.SearchTool(stores=all_stores, search_type=type)(query, top_k)


@mcp_app.tool()
def list_chunks(
    filename: str,
    store: Casebase = Depends(_get_mcp_user_store),
) -> list[ChunkSummary] | None:
    """List chunk metadata for a document by relative path."""
    return tools.ListChunksTool(
        path=store.chunks_dir(settings.data_dir),
    )(filename)


@mcp_app.tool()
def get_chunk(
    filename: str,
    chunk_index: int,
    store: Casebase = Depends(_get_mcp_user_store),
) -> str | None:
    """Get the text content of a specific chunk by relative document path."""
    return tools.GetChunkTool(
        path=store.chunks_dir(settings.data_dir),
    )(filename, chunk_index)


@mcp_app.tool()
async def explore_documents(
    task: str,
    ctx: Context,
    user_id: str = Depends(_get_mcp_user_id),
    store: Casebase = Depends(_get_mcp_user_store),
    group_stores: tuple[Casebase, ...] = Depends(_get_mcp_group_stores),
) -> str | None:
    """Explore documents using a subagent.

    Delegates to a subagent that can list, search, and read documents.
    Returns a summary of findings. Uses the server's LLM when configured,
    otherwise falls back to MCP client sampling.

    Args:
        task: Natural language description of what to explore or find.
    """
    model_name = settings.llm.small_model or settings.llm.model

    if model_name:
        result = await explore_agent.run(
            task,
            model=OpenAIResponsesModel(
                model_name,
                provider=OpenAIProvider(
                    api_key=settings.llm.api_key,
                    base_url=settings.llm.base_url or None,
                ),
            ),
            deps=UserDeps(
                user_id=user_id,
                store=store,
                group_stores=group_stores,
            ),
            toolsets=[explore_toolset],
            instructions=EXPLORE_INSTRUCTIONS,
        )
        return result.output

    docs_dir = store.documents_dir(settings.data_dir)
    all_stores = (store, *group_stores)

    result = await ctx.sample(
        task,
        system_prompt=EXPLORE_INSTRUCTIONS,
        tools=[
            tools.ListDocumentsTool(path=docs_dir),
            tools.GlobDocumentsTool(path=docs_dir),
            tools.GrepTool(path=docs_dir),
            tools.SearchTool(stores=all_stores, search_type="dense"),
            tools.SearchTool(stores=all_stores, search_type="sparse"),
            tools.GetDocumentLinesTool(path=docs_dir),
        ],
    )
    return result.text


@mcp_app.tool()
async def edit_document(
    filename: str,
    old_string: str,
    new_string: str,
    ctx: Context,
    store: Casebase = Depends(_get_mcp_user_store),
) -> str:
    """Edit a document by replacing an exact string.

    Asks the user for confirmation before modifying the file.
    The old_string must appear exactly once in the file.

    Args:
        filename: The relative document path.
        old_string: The exact text to replace. Must appear exactly once.
        new_string: The replacement text.
    """
    response = await ctx.elicit(
        message=(
            f"Allow edit to '{filename}'?\n\n"
            f"Replace:\n{old_string!r}\n\nWith:\n{new_string!r}"
        ),
        response_type=None,
    )
    if response.action != "accept":
        return "Edit denied by user."

    return tools.EditDocumentTool(
        path=store.documents_dir(settings.data_dir),
        store=store,
    )(filename, old_string, new_string)


@mcp_app.tool()
async def write_document(
    filename: str,
    content: str,
    ctx: Context,
    mode: Literal["prepend", "append", "replace"] = "replace",
    store: Casebase = Depends(_get_mcp_user_store),
) -> str:
    """Write content to a document (prepend, append, or replace).

    Asks the user for confirmation before modifying the file.

    Args:
        filename: The relative document path.
        content: The text content to write.
        mode: "replace" overwrites (creates if absent), "append" adds to end,
            "prepend" adds to start.
    """
    action = "Create/overwrite" if mode == "replace" else mode.capitalize()
    response = await ctx.elicit(
        message=f"Allow {action} '{filename}' ({len(content)} chars)?",
        response_type=None,
    )
    if response.action != "accept":
        return "Write denied by user."

    return tools.WriteDocumentTool(
        path=store.documents_dir(settings.data_dir),
        store=store,
    )(filename, content, mode)
