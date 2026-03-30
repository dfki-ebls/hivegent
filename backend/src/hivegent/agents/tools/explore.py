"""Explore-oriented agent tool registrations."""

from pydantic_ai import FunctionToolset

from ...chunkers.base import RetrievedChunk
from ...chunks import GetChunkTool, ListChunksTool
from ...config import settings
from ...retrieval import build_search_tool
from ...store import Casebase, build_search_paths
from ...tools import (
    GetDocumentLinesTool,
    GetDocumentTool,
    GlobDocumentsTool,
    GrepTool,
    LanceDBSearchTool,
    ListDocumentsTool,
    SearchPath,
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


def _metadata_paths(deps: UserDeps) -> tuple[SearchPath, ...]:
    return build_search_paths(
        deps.store,
        deps.group_stores,
        settings.data_dir,
        dir_fn=Casebase.metadata_dir,
        filter_for_store=deps.filter_for_store,
    )


def _list_documents(deps: UserDeps) -> ListDocumentsTool:
    return ListDocumentsTool(paths=_workspace_paths(deps))


def _glob_documents(deps: UserDeps) -> GlobDocumentsTool:
    return GlobDocumentsTool(paths=_workspace_paths(deps))


def _grep(deps: UserDeps) -> GrepTool:
    return GrepTool(paths=_workspace_paths(deps))


def _get_document_lines(deps: UserDeps) -> GetDocumentLinesTool:
    return GetDocumentLinesTool(paths=_workspace_paths(deps))


def _get_document(deps: UserDeps) -> GetDocumentTool:
    return GetDocumentTool(paths=_workspace_paths(deps))


def _list_chunks(deps: UserDeps) -> ListChunksTool:
    return ListChunksTool(paths=_metadata_paths(deps))


def _get_chunk(deps: UserDeps) -> GetChunkTool:
    return GetChunkTool(paths=_metadata_paths(deps))


def _semantic_search(deps: UserDeps) -> LanceDBSearchTool[RetrievedChunk]:
    return build_search_tool(
        deps.all_stores, filter_for_store=deps.filter_for_store
    )


explore_toolset: FunctionToolset[UserDeps] = FunctionToolset()

register_agent_tools(
    explore_toolset,
    UserDeps,
    [
        _list_documents,
        _glob_documents,
        _grep,
        _get_document_lines,
        _get_document,
        _list_chunks,
        _get_chunk,
        _semantic_search,
    ],
)
