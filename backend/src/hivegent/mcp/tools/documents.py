"""Document-oriented MCP tool registrations."""

from fastmcp.dependencies import Depends  # pyright: ignore[reportAttributeAccessIssue]

from ... import tool_runtime
from ...store import Casebase
from ...tools import (
    DocumentRange,
    DocumentSummary,
    GetDocumentLinesTool,
    GetDocumentTool,
    GlobDocumentsTool,
    GrepMatch,
    GrepTool,
    ListDocumentsTool,
)
from ...tools.base import tool_description
from ...tools.documents import (
    DocumentEndLineArg,
    DocumentFilenameArg,
    DocumentMaxDepthArg,
    DocumentStartLineArg,
    DocumentSubdirArg,
    GlobPatternArg,
)
from ...tools.grep import ContextLinesArg, GrepGlobArg, GrepPatternArg
from ..app import mcp_app
from ..common import get_mcp_user_store

__all__ = [
    "get_document",
    "get_document_lines",
    "glob_documents",
    "grep",
    "list_documents",
]


@mcp_app.tool(description=tool_description(ListDocumentsTool))
def list_documents(
    subdir: DocumentSubdirArg = None,
    max_depth: DocumentMaxDepthArg = None,
    store: Casebase = Depends(get_mcp_user_store),
) -> list[DocumentSummary]:
    return tool_runtime.list_documents(
        store,
        subdir=subdir,
        max_depth=max_depth,
    )


@mcp_app.tool(description=tool_description(GetDocumentTool))
def get_document(
    filename: DocumentFilenameArg,
    store: Casebase = Depends(get_mcp_user_store),
) -> str | None:
    return tool_runtime.get_document(store, filename)


@mcp_app.tool(description=tool_description(GetDocumentLinesTool))
def get_document_lines(
    filename: DocumentFilenameArg,
    start: DocumentStartLineArg = 1,
    end: DocumentEndLineArg = None,
    store: Casebase = Depends(get_mcp_user_store),
) -> DocumentRange | None:
    return tool_runtime.get_document_lines(
        store,
        filename,
        start=start,
        end=end,
    )


@mcp_app.tool(description=tool_description(GlobDocumentsTool))
def glob_documents(
    pattern: GlobPatternArg,
    store: Casebase = Depends(get_mcp_user_store),
) -> list[str]:
    return tool_runtime.glob_documents(store, pattern)


@mcp_app.tool(description=tool_description(GrepTool))
async def grep(
    pattern: GrepPatternArg,
    glob: GrepGlobArg = None,
    context_lines: ContextLinesArg = 0,
    store: Casebase = Depends(get_mcp_user_store),
) -> list[GrepMatch]:
    return await tool_runtime.grep(
        store,
        pattern,
        glob=glob,
        context_lines=context_lines,
    )
