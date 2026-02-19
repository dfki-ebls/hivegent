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


def _rechunk(user_id: str, filename: str) -> None:
    """Re-chunk a document and sync the search index after a write."""
    from .chunks import chunk_document
    from .retrieval import sync_index

    docs_dir = settings.get_user_documents_dir(user_id)
    file_path = docs_dir / filename
    try:
        text_content = file_path.read_text(encoding="utf-8")
        chunk_document(user_id, filename, text_content)
        sync_index(user_id)
    except Exception:
        logger.warning("Re-chunking failed for %s after write", filename)


@mcp_app.tool()
def list_documents(
    subdir: str | None = None,
    max_depth: int | None = None,
    user_id: str = Depends(_get_mcp_user_id),
) -> list[DocumentSummary]:
    """List all available documents with their sizes in bytes."""
    return tools.ListDocumentsTool(path=settings.get_user_documents_dir(user_id))(
        subdir=subdir,
        max_depth=max_depth,
    )


@mcp_app.tool()
def get_document(
    filename: str,
    user_id: str = Depends(_get_mcp_user_id),
) -> str | None:
    """Get the full content of a specific document by relative path."""
    return tools.GetDocumentTool(path=settings.get_user_documents_dir(user_id))(
        filename
    )


@mcp_app.tool()
def get_document_lines(
    filename: str,
    start: int = 1,
    end: int | None = None,
    user_id: str = Depends(_get_mcp_user_id),
) -> DocumentRange | None:
    """Get a range of lines from a document by relative path."""
    return tools.GetDocumentLinesTool(path=settings.get_user_documents_dir(user_id))(
        filename,
        start,
        end,
    )


@mcp_app.tool()
def glob_documents(
    pattern: str,
    user_id: str = Depends(_get_mcp_user_id),
) -> list[str]:
    """Find documents matching a glob pattern."""
    return tools.GlobDocumentsTool(path=settings.get_user_documents_dir(user_id))(
        pattern
    )


@mcp_app.tool()
def grep(
    pattern: str,
    glob: str | None = None,
    context_lines: int = 0,
    include_content: bool = True,
    user_id: str = Depends(_get_mcp_user_id),
) -> list[GrepMatch]:
    """Search documents for a pattern."""
    return tools.GrepTool(path=settings.get_user_documents_dir(user_id))(
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
    user_id: str = Depends(_get_mcp_user_id),
) -> list[RetrievedChunk]:
    """Search chunks using semantic similarity or keyword matching.

    Use "dense" for vector embeddings (conceptual queries),
    "sparse" for BM25/FTS (keyword queries).
    """
    return tools.SearchTool(user_id=user_id, search_type=type)(
        query,
        top_k,
    )


@mcp_app.tool()
def list_chunks(
    filename: str,
    user_id: str = Depends(_get_mcp_user_id),
) -> list[ChunkSummary] | None:
    """List chunk metadata for a document by relative path."""
    return tools.ListChunksTool(path=settings.get_user_chunks_dir(user_id))(filename)


@mcp_app.tool()
def get_chunk(
    filename: str,
    chunk_index: int,
    user_id: str = Depends(_get_mcp_user_id),
) -> str | None:
    """Get the text content of a specific chunk by relative document path."""
    return tools.GetChunkTool(path=settings.get_user_chunks_dir(user_id))(
        filename,
        chunk_index,
    )


@mcp_app.tool()
async def explore_documents(
    task: str,
    ctx: Context,
    user_id: str = Depends(_get_mcp_user_id),
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
            deps=UserDeps(user_id=user_id),
            toolsets=[explore_toolset],
            instructions=EXPLORE_INSTRUCTIONS,
        )
        return result.output

    docs_dir = settings.get_user_documents_dir(user_id)

    result = await ctx.sample(
        task,
        system_prompt=EXPLORE_INSTRUCTIONS,
        tools=[
            tools.ListDocumentsTool(path=docs_dir),
            tools.GlobDocumentsTool(path=docs_dir),
            tools.GrepTool(path=docs_dir),
            tools.SearchTool(user_id=user_id, search_type="dense"),
            tools.SearchTool(user_id=user_id, search_type="sparse"),
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
    user_id: str = Depends(_get_mcp_user_id),
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

    docs_dir = settings.get_user_documents_dir(user_id)
    result = tools.EditDocumentTool(path=docs_dir)(filename, old_string, new_string)

    if not result.startswith("Error:"):
        _rechunk(user_id, filename)

    return result


@mcp_app.tool()
async def write_document(
    filename: str,
    content: str,
    ctx: Context,
    mode: Literal["prepend", "append", "replace"] = "replace",
    user_id: str = Depends(_get_mcp_user_id),
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

    docs_dir = settings.get_user_documents_dir(user_id)
    result = tools.WriteDocumentTool(path=docs_dir)(filename, content, mode)

    if not result.startswith("Error:"):
        _rechunk(user_id, filename)

    return result
