"""Explore-oriented agent tool registrations."""

from pydantic_ai import FunctionToolset

from ...chunkers.base import RetrievedChunk
from ...config import settings
from ...retrieval import build_search_tool
from ...tools import (
    GlobDocumentsTool,
    GrepTool,
    ListDocumentsTool,
    QueryTableTool,
    ReadBinaryDocumentTool,
    ReadDocumentTool,
    VectorSearchTool,
)
from ...tools.pydantic_ai import register_agent_tools
from ..common import UserDeps
from .write import output_sink, validate_output_path

__all__ = ["explore_toolset"]


# Each of these builds with the writer its redirect commits through, which is
# `None` outside a writing mode: what a tool may do with its result is settled
# when the tool is built, not by the framework it is handed to.
def _list_documents(deps: UserDeps) -> ListDocumentsTool:
    return ListDocumentsTool(paths=deps.search_paths(), writer=output_sink(deps))


def _glob_documents(deps: UserDeps) -> GlobDocumentsTool:
    return GlobDocumentsTool(paths=deps.search_paths(), writer=output_sink(deps))


def _read_document(deps: UserDeps) -> ReadDocumentTool:
    return ReadDocumentTool(paths=deps.search_paths(), writer=output_sink(deps))


def _read_binary_document(deps: UserDeps) -> ReadBinaryDocumentTool:
    return ReadBinaryDocumentTool(
        paths=deps.search_paths(),
        binary_content_mode=settings.multimodal.binary_content,
    )


def _query_table(deps: UserDeps) -> QueryTableTool:
    return QueryTableTool(paths=deps.search_paths(), writer=output_sink(deps))


def _grep(deps: UserDeps) -> GrepTool:
    return GrepTool(paths=deps.search_paths(), writer=output_sink(deps))


def _search(deps: UserDeps) -> VectorSearchTool[RetrievedChunk]:
    return build_search_tool(
        deps.all_stores,
        filter_for_store=deps.filter_for_store,
        writer=output_sink(deps),
    )


explore_toolset: FunctionToolset[UserDeps] = FunctionToolset()

# Every tool here answers a question whose result can dwarf the answer, so each
# takes the redirect argument — except the binary reader, whose result is an
# attachment the model looks at rather than text a later step could process.
register_agent_tools(
    explore_toolset,
    UserDeps,
    [
        _list_documents,
        _glob_documents,
        _read_document,
        _read_binary_document,
        _query_table,
        _grep,
        _search,
    ],
    args_validator=validate_output_path,
)
