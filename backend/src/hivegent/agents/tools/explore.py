"""Explore-oriented agent tool registrations."""

from pydantic_ai import FunctionToolset

from ...chunkers.base import RetrievedChunk
from ...config import settings
from ...retrieval import build_search_tool
from ...store import build_search_paths
from ...tools import (
    GlobDocumentsTool,
    GrepTool,
    ListDocumentsTool,
    ReadBinaryDocumentTool,
    ReadDocumentTool,
    SearchPath,
    VectorSearchTool,
)
from ...tools.pydantic_ai import register_agent_tools
from ..common import UserDeps

__all__ = ["explore_toolset"]


def _workspace_paths(deps: UserDeps) -> tuple[SearchPath, ...]:
    return build_search_paths(
        deps.store,
        deps.group_stores,
        settings.data_dir,
        filter_for_store=deps.filter_for_store,
    )


def _list_documents(deps: UserDeps) -> ListDocumentsTool:
    return ListDocumentsTool(paths=_workspace_paths(deps))


def _glob_documents(deps: UserDeps) -> GlobDocumentsTool:
    return GlobDocumentsTool(paths=_workspace_paths(deps))


def _read_document(deps: UserDeps) -> ReadDocumentTool:
    return ReadDocumentTool(paths=_workspace_paths(deps))


def _read_binary_document(deps: UserDeps) -> ReadBinaryDocumentTool:
    return ReadBinaryDocumentTool(paths=_workspace_paths(deps))


def _grep(deps: UserDeps) -> GrepTool:
    return GrepTool(paths=_workspace_paths(deps))


def _search(deps: UserDeps) -> VectorSearchTool[RetrievedChunk]:
    return build_search_tool(deps.all_stores, filter_for_store=deps.filter_for_store)


explore_toolset: FunctionToolset[UserDeps] = FunctionToolset()

register_agent_tools(
    explore_toolset,
    UserDeps,
    [
        _list_documents,
        _glob_documents,
        _read_document,
        _read_binary_document,
        _grep,
        _search,
    ],
)
