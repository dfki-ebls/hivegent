"""Document-oriented MCP tool registrations."""

from fastmcp.dependencies import Depends

from ...config import settings
from ...store import Casebase, build_search_paths
from ...tools import (
    GlobDocumentsTool,
    GrepTool,
    JqTool,
    ListDocumentsTool,
    QueryTableTool,
    ReadBinaryDocumentTool,
    ReadDocumentTool,
    SearchPath,
)
from ...tools.fastmcp import register_mcp_tools
from ...tools.sink import OutputPathArg
from ..app import mcp_app
from ..common import get_mcp_group_stores, get_mcp_user_store

__all__: list[str] = []


def _search_paths(
    store: Casebase = Depends(get_mcp_user_store),
    group_stores: tuple[Casebase, ...] = Depends(get_mcp_group_stores),
) -> tuple[SearchPath, ...]:
    """The roots every read tool on this surface is scoped to.

    One dependency rather than the same two stores injected per registration,
    so the span a tool may reach is stated once and a factory below declares
    only what it adds to it.
    """
    return build_search_paths(store, group_stores, settings.data_dir)


def _list_documents(
    paths: tuple[SearchPath, ...] = Depends(_search_paths),
) -> ListDocumentsTool:
    return ListDocumentsTool(paths=paths)


def _glob_documents(
    paths: tuple[SearchPath, ...] = Depends(_search_paths),
) -> GlobDocumentsTool:
    return GlobDocumentsTool(paths=paths)


def _read_document(
    paths: tuple[SearchPath, ...] = Depends(_search_paths),
) -> ReadDocumentTool:
    return ReadDocumentTool(paths=paths)


def _query_table(
    paths: tuple[SearchPath, ...] = Depends(_search_paths),
) -> QueryTableTool:
    return QueryTableTool(paths=paths)


def _jq(paths: tuple[SearchPath, ...] = Depends(_search_paths)) -> JqTool:
    return JqTool(paths=paths)


def _read_binary_document(
    paths: tuple[SearchPath, ...] = Depends(_search_paths),
) -> ReadBinaryDocumentTool:
    return ReadBinaryDocumentTool(
        paths=paths,
        binary_content_mode=settings.multimodal.binary_content,
    )


def _grep(paths: tuple[SearchPath, ...] = Depends(_search_paths)) -> GrepTool:
    return GrepTool(paths=paths)


# These tools are built with no writer, so the redirect they declare cannot
# be honoured here and is left out rather than advertised and refused: every
# MCP workspace write goes behind an elicitation the generated wrapper has no
# way to raise, and the guidance that makes a redirect worth using is the
# agent's prompt, which no MCP client is handed.
register_mcp_tools(
    mcp_app,
    [
        _list_documents,
        _glob_documents,
        _read_document,
        _read_binary_document,
        _query_table,
        _jq,
        _grep,
    ],
    omit=(OutputPathArg,),
)
