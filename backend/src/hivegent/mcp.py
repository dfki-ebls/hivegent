"""FastMCP server with OIDCProxy auth and explicit typed tool wrappers."""

import logging
from typing import Literal

import httpx
from fastmcp import Context, FastMCP
from fastmcp.dependencies import (
    CurrentAccessToken,
    Depends,  # pyright: ignore[reportAttributeAccessIssue]
)
from fastmcp.server.auth import AccessToken, OIDCProxy
from pydantic_ai.mcp import MCPServerStreamableHTTP
from pydantic_ai.models.openai import OpenAIResponsesModel
from pydantic_ai.providers.openai import OpenAIProvider

from .agents import UserDeps, explore_toolset, user_agent
from .auth import auth_settings
from .config import settings
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
from .retrieval import build_search_tool
from .tools import (
    DocumentRange,
    DocumentSummary,
    GetChunkTool,
    GetDocumentLinesTool,
    GetDocumentTool,
    GlobDocumentsTool,
    GrepMatch,
    GrepTool,
    ListChunksTool,
    ListDocumentsTool,
)
from .types import ChunkSummary, McpServerConfig, RetrievedChunk

__all__ = ["build_mcp_server", "mcp_app"]

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# MCP auth setup
# ---------------------------------------------------------------------------

mcp_auth: OIDCProxy | None = None

if not auth_settings.disabled:
    mcp_auth = OIDCProxy(
        config_url=f"{auth_settings.issuer}/.well-known/openid-configuration",
        client_id=settings.mcp.client_id,
        client_secret=settings.mcp.client_secret,
        base_url=settings.mcp.base_url,
    )

mcp_app = FastMCP("Hivegent", auth=mcp_auth)


# ---------------------------------------------------------------------------
# Auth helpers
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# MCP tool endpoints
# ---------------------------------------------------------------------------


@mcp_app.tool(description=ListDocumentsTool.__call__.__doc__)
def list_documents(
    subdir: str | None = None,
    max_depth: int | None = None,
    store: Casebase = Depends(_get_mcp_user_store),
) -> list[DocumentSummary]:
    return list_document_summaries(
        store,
        subdir=subdir,
        max_depth=max_depth,
    )


@mcp_app.tool(description=GetDocumentTool.__call__.__doc__)
def get_document(
    filename: str,
    store: Casebase = Depends(_get_mcp_user_store),
) -> str | None:
    return get_document_text(store, filename)


@mcp_app.tool(description=GetDocumentLinesTool.__call__.__doc__)
def get_document_lines(
    filename: str,
    start: int = 1,
    end: int | None = None,
    store: Casebase = Depends(_get_mcp_user_store),
) -> DocumentRange | None:
    return get_document_lines_for_store(
        store,
        filename,
        start=start,
        end=end,
    )


@mcp_app.tool(description=GlobDocumentsTool.__call__.__doc__)
def glob_documents(
    pattern: str,
    store: Casebase = Depends(_get_mcp_user_store),
) -> list[str]:
    return glob_documents_for_store(store, pattern)


@mcp_app.tool(description=GrepTool.__call__.__doc__)
async def grep(
    pattern: str,
    glob: str | None = None,
    context_lines: int = 0,
    store: Casebase = Depends(_get_mcp_user_store),
) -> list[GrepMatch]:
    return await grep_documents(
        store,
        pattern,
        glob=glob,
        context_lines=context_lines,
    )


@mcp_app.tool()
def semantic_search(
    query: str,
    type: Literal["dense", "sparse", "hybrid"] = "hybrid",
    top_k: int = 5,
    store: Casebase = Depends(_get_mcp_user_store),
    group_stores: tuple[Casebase, ...] = Depends(_get_mcp_group_stores),
) -> list[RetrievedChunk]:
    """Search chunks using semantic similarity or keyword matching.

    Searches across personal documents and all group casebases.
    Use "dense" for vector embeddings (conceptual queries),
    "sparse" for BM25/FTS (keyword queries),
    "hybrid" for combined vector + keyword search.
    """
    return semantic_search_documents(
        store,
        query,
        type=type,
        top_k=top_k,
        group_stores=group_stores,
    )


@mcp_app.tool(description=ListChunksTool.__call__.__doc__)
def list_chunks(
    filename: str,
    store: Casebase = Depends(_get_mcp_user_store),
) -> list[ChunkSummary] | None:
    return list_document_chunks(store, filename)


@mcp_app.tool(description=GetChunkTool.__call__.__doc__)
def get_chunk(
    filename: str,
    chunk_index: int,
    store: Casebase = Depends(_get_mcp_user_store),
) -> str | None:
    return get_document_chunk(store, filename, chunk_index)


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
        result = await user_agent.run(
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

    workspace = store.workspace_dir(settings.data_dir)
    all_stores = (store, *group_stores)
    result = await ctx.sample(
        task,
        system_prompt=EXPLORE_INSTRUCTIONS,
        tools=[
            ListDocumentsTool(path=workspace, extension=""),
            GlobDocumentsTool(path=workspace, extension=""),
            GrepTool(path=workspace),
            build_search_tool(all_stores),
            GetDocumentLinesTool(path=workspace),
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

    return await edit_document_text(store, filename, old_string, new_string)


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

    return await write_document_text(store, filename, content, mode=mode)


# ---------------------------------------------------------------------------
# MCP server builder (for user-provided external MCP servers)
# ---------------------------------------------------------------------------


def build_mcp_server(server_cfg: McpServerConfig) -> MCPServerStreamableHTTP:
    """Build an MCP server toolset from a user-provided config.

    Uses Streamable HTTP transport.  When OAuth2 client credentials are
    configured, the connection is authenticated via
    ``ClientCredentialsOAuthProvider``.

    Args:
        server_cfg: User-provided MCP server configuration.

    Returns:
        A configured ``MCPServerStreamableHTTP`` instance.
    """
    if server_cfg.oauth2:
        import warnings

        from fastmcp.client.auth.oauth import TokenStorageAdapter
        from key_value.aio.stores.memory import MemoryStore
        from mcp.client.auth.extensions.client_credentials import (
            ClientCredentialsOAuthProvider,
        )

        store = MemoryStore()
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            storage = TokenStorageAdapter(store, server_url=server_cfg.url)
        oauth_provider = ClientCredentialsOAuthProvider(
            server_url=server_cfg.url,
            storage=storage,
            client_id=server_cfg.oauth2.client_id,
            client_secret=server_cfg.oauth2.client_secret,
            scopes=server_cfg.oauth2.scopes,
        )
        http_client = httpx.AsyncClient(auth=oauth_provider)
        return MCPServerStreamableHTTP(
            url=server_cfg.url,
            http_client=http_client,
            tool_prefix=server_cfg.tool_prefix,
        )

    return MCPServerStreamableHTTP(
        url=server_cfg.url,
        headers=server_cfg.headers or {},
        tool_prefix=server_cfg.tool_prefix,
    )
