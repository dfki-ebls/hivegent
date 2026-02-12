"""FastMCP server with OIDCProxy auth and explicit typed tool wrappers."""

from docket.dependencies import Depends
from fastmcp import Context, FastMCP
from fastmcp.server.auth import AccessToken, OIDCProxy
from fastmcp.server.dependencies import get_access_token
from pydantic_ai.models.openai import OpenAIResponsesModel
from pydantic_ai.providers.openai import OpenAIProvider

from . import tools
from .agent import UserDeps, explore_agent, explore_toolset
from .prompts import EXPLORE_INSTRUCTIONS
from .auth import auth_settings
from .config import settings
from .types import (
    ChunkSummary,
    DocumentRange,
    DocumentSummary,
    GrepMatch,
    RetrievedChunk,
    RetrievedDocument,
)

__all__ = ["mcp_app"]


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
    access_token: AccessToken | None = Depends(get_access_token),
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
def search_documents(
    query: str,
    top_k: int = 3,
    subdir: str | None = None,
    max_depth: int | None = None,
    user_id: str = Depends(_get_mcp_user_id),
) -> list[RetrievedDocument]:
    """Semantic search for documents using BM25 ranking."""
    return tools.SearchDocumentsTool(path=settings.get_user_documents_dir(user_id))(
        query,
        top_k,
        subdir=subdir,
        max_depth=max_depth,
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
def search_chunks(
    query: str,
    top_k: int = 5,
    subdir: str | None = None,
    max_depth: int | None = None,
    user_id: str = Depends(_get_mcp_user_id),
) -> list[RetrievedChunk]:
    """Search across all document chunks using BM25 ranking."""
    return tools.SearchChunksTool(path=settings.get_user_chunks_dir(user_id))(
        query,
        top_k,
        subdir=subdir,
        max_depth=max_depth,
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
    chunks_dir = settings.get_user_chunks_dir(user_id)

    result = await ctx.sample(
        task,
        system_prompt=EXPLORE_INSTRUCTIONS,
        tools=[
            tools.ListDocumentsTool(path=docs_dir),
            tools.GlobDocumentsTool(path=docs_dir),
            tools.GrepTool(path=docs_dir),
            tools.SearchDocumentsTool(path=docs_dir),
            tools.SearchChunksTool(path=chunks_dir),
            tools.GetDocumentLinesTool(path=docs_dir),
        ],
    )
    return result.text
