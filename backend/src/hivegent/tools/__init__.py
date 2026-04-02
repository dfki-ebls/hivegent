"""Tools subpackage — self-contained, framework-free tool implementations."""

from .documents import (
    DocumentRange,
    DocumentSummary,
    DocumentTreeNode,
    GetDocumentLinesTool,
    GetDocumentTool,
    GlobDocumentsTool,
    ListDocumentsTool,
    TreeDocumentsTool,
)
from .grep import GrepMatch, GrepTool
from .jq import JqTool
from .mutations import EditDocumentTool, WriteDocumentTool
from .plan import CreatePlanTool
from .retrieval import IndexedStorage, LanceDBSearchTool, SearchResult, SearchType
from .base import SearchPath, SearchPathFilterFunc, Tool, file_allowed, tool_name
from .web import WebFetch, WebSearch

__all__ = [
    "CreatePlanTool",
    "DocumentRange",
    "DocumentSummary",
    "DocumentTreeNode",
    "EditDocumentTool",
    "GetDocumentLinesTool",
    "GetDocumentTool",
    "GlobDocumentsTool",
    "GrepMatch",
    "GrepTool",
    "IndexedStorage",
    "JqTool",
    "LanceDBSearchTool",
    "ListDocumentsTool",
    "SearchPath",
    "SearchPathFilterFunc",
    "SearchResult",
    "SearchType",
    "Tool",
    "TreeDocumentsTool",
    "file_allowed",
    "tool_name",
    "WebFetch",
    "WebSearch",
    "WriteDocumentTool",
]
