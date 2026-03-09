"""Explore-oriented agent tool registrations."""

from pydantic_ai import FunctionToolset

from ...chunkers.base import RetrievedChunk
from ...chunks import GetChunkTool, ListChunksTool
from ...config import settings
from ...retrieval import build_search_tool
from ...tools import (
    GetDocumentLinesTool,
    GetDocumentTool,
    GlobDocumentsTool,
    GrepTool,
    LanceDBSearchTool,
    ListDocumentsTool,
)
from ...tools.pydantic_ai import register_agent_tools
from ..common import UserDeps

__all__ = ["explore_toolset"]


def _list_documents(deps: UserDeps) -> ListDocumentsTool:
    return ListDocumentsTool(
        path=deps.store.workspace_dir(settings.data_dir),
        file_filter=deps.document_filter,
    )


def _glob_documents(deps: UserDeps) -> GlobDocumentsTool:
    return GlobDocumentsTool(
        path=deps.store.workspace_dir(settings.data_dir),
        file_filter=deps.document_filter,
    )


def _grep(deps: UserDeps) -> GrepTool:
    return GrepTool(
        path=deps.store.workspace_dir(settings.data_dir),
        file_filter=deps.document_filter,
    )


def _get_document_lines(deps: UserDeps) -> GetDocumentLinesTool:
    return GetDocumentLinesTool(
        path=deps.store.workspace_dir(settings.data_dir),
        file_filter=deps.document_filter,
    )


def _get_document(deps: UserDeps) -> GetDocumentTool:
    return GetDocumentTool(
        path=deps.store.workspace_dir(settings.data_dir),
        file_filter=deps.document_filter,
    )


def _list_chunks(deps: UserDeps) -> ListChunksTool:
    return ListChunksTool(
        metadata_dir=deps.store.metadata_dir(settings.data_dir),
        file_filter=deps.document_filter,
    )


def _get_chunk(deps: UserDeps) -> GetChunkTool:
    return GetChunkTool(
        metadata_dir=deps.store.metadata_dir(settings.data_dir),
        file_filter=deps.document_filter,
    )


def _semantic_search(deps: UserDeps) -> LanceDBSearchTool[RetrievedChunk]:
    return build_search_tool(deps.all_stores, file_filter=deps.document_filter)


explore_toolset: FunctionToolset[UserDeps] = FunctionToolset()

register_agent_tools(explore_toolset, UserDeps, [
    _list_documents,
    _glob_documents,
    _grep,
    _get_document_lines,
    _get_document,
    _list_chunks,
    _get_chunk,
    _semantic_search,
])
