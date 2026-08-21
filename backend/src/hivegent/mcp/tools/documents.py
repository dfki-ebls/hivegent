"""Document-oriented MCP tool registrations."""

from fastmcp.dependencies import Depends  # pyright: ignore[reportAttributeAccessIssue]

from ...config import settings
from ...store import Casebase, build_search_paths
from ...tools import (
    GlobDocumentsTool,
    GrepTool,
    ListDocumentsTool,
    QueryTableTool,
    ReadBinaryDocumentTool,
    ReadDocumentTool,
)
from ...tools.fastmcp import register_mcp_tools
from ..app import mcp_app
from ..common import get_mcp_group_stores, get_mcp_user_store

__all__: list[str] = []


def _list_documents(
    store: Casebase = Depends(get_mcp_user_store),
    group_stores: tuple[Casebase, ...] = Depends(get_mcp_group_stores),
) -> ListDocumentsTool:
    return ListDocumentsTool(
        paths=build_search_paths(store, group_stores, settings.data_dir)
    )


def _glob_documents(
    store: Casebase = Depends(get_mcp_user_store),
    group_stores: tuple[Casebase, ...] = Depends(get_mcp_group_stores),
) -> GlobDocumentsTool:
    return GlobDocumentsTool(
        paths=build_search_paths(store, group_stores, settings.data_dir)
    )


def _read_document(
    store: Casebase = Depends(get_mcp_user_store),
    group_stores: tuple[Casebase, ...] = Depends(get_mcp_group_stores),
) -> ReadDocumentTool:
    return ReadDocumentTool(
        paths=build_search_paths(store, group_stores, settings.data_dir)
    )


def _query_table(
    store: Casebase = Depends(get_mcp_user_store),
    group_stores: tuple[Casebase, ...] = Depends(get_mcp_group_stores),
) -> QueryTableTool:
    return QueryTableTool(
        paths=build_search_paths(store, group_stores, settings.data_dir)
    )


def _read_binary_document(
    store: Casebase = Depends(get_mcp_user_store),
    group_stores: tuple[Casebase, ...] = Depends(get_mcp_group_stores),
) -> ReadBinaryDocumentTool:
    return ReadBinaryDocumentTool(
        paths=build_search_paths(store, group_stores, settings.data_dir),
        binary_content_mode=settings.multimodal.binary_content,
    )


def _grep(
    store: Casebase = Depends(get_mcp_user_store),
    group_stores: tuple[Casebase, ...] = Depends(get_mcp_group_stores),
) -> GrepTool:
    return GrepTool(paths=build_search_paths(store, group_stores, settings.data_dir))


register_mcp_tools(
    mcp_app,
    [
        _list_documents,
        _glob_documents,
        _read_document,
        _read_binary_document,
        _query_table,
        _grep,
    ],
)
