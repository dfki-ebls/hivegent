"""Document-oriented MCP tool registrations."""

from fastmcp.dependencies import Depends  # pyright: ignore[reportAttributeAccessIssue]

from ...config import settings
from ...store import Casebase, build_search_paths
from ...tools import (
    GrepTool,
    ListDocumentsTool,
    ReadDocumentTool,
)
from ..app import mcp_app
from ..common import get_mcp_group_stores, get_mcp_user_store
from ...tools.fastmcp import register_mcp_tools

__all__: list[str] = []


def _list_documents(
    store: Casebase = Depends(get_mcp_user_store),
    group_stores: tuple[Casebase, ...] = Depends(get_mcp_group_stores),
) -> ListDocumentsTool:
    return ListDocumentsTool(
        paths=build_search_paths(store, group_stores, settings.data_dir)
    )


def _read_document(
    store: Casebase = Depends(get_mcp_user_store),
    group_stores: tuple[Casebase, ...] = Depends(get_mcp_group_stores),
) -> ReadDocumentTool:
    return ReadDocumentTool(
        paths=build_search_paths(store, group_stores, settings.data_dir)
    )


def _grep(
    store: Casebase = Depends(get_mcp_user_store),
    group_stores: tuple[Casebase, ...] = Depends(get_mcp_group_stores),
) -> GrepTool:
    return GrepTool(
        paths=build_search_paths(store, group_stores, settings.data_dir)
    )


register_mcp_tools(
    mcp_app,
    [
        _list_documents,
        _read_document,
        _grep,
    ],
)
