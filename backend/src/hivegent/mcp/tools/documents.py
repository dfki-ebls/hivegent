"""Document-oriented MCP tool registrations."""

from fastmcp.dependencies import Depends  # pyright: ignore[reportAttributeAccessIssue]

from ...config import settings
from ...store import Casebase
from ...tools import (
    GetDocumentLinesTool,
    GetDocumentTool,
    GlobDocumentsTool,
    GrepTool,
    ListDocumentsTool,
)
from ..app import mcp_app
from ..common import get_mcp_user_store
from ...tools.fastmcp import register_mcp_tools

__all__: list[str] = []


def _list_documents(
    store: Casebase = Depends(get_mcp_user_store),
) -> ListDocumentsTool:
    return ListDocumentsTool(path=store.workspace_dir(settings.data_dir))


def _get_document(
    store: Casebase = Depends(get_mcp_user_store),
) -> GetDocumentTool:
    return GetDocumentTool(path=store.workspace_dir(settings.data_dir))


def _get_document_lines(
    store: Casebase = Depends(get_mcp_user_store),
) -> GetDocumentLinesTool:
    return GetDocumentLinesTool(path=store.workspace_dir(settings.data_dir))


def _glob_documents(
    store: Casebase = Depends(get_mcp_user_store),
) -> GlobDocumentsTool:
    return GlobDocumentsTool(path=store.workspace_dir(settings.data_dir))


def _grep(
    store: Casebase = Depends(get_mcp_user_store),
) -> GrepTool:
    return GrepTool(path=store.workspace_dir(settings.data_dir))


register_mcp_tools(mcp_app, [
    _list_documents,
    _get_document,
    _get_document_lines,
    _glob_documents,
    _grep,
])
