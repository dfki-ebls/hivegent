"""Tools subpackage — self-contained, framework-free tool implementations."""

from .base import (
    DEFAULT_EXCLUDE_DIRS,
    BinaryAttachment,
    SearchPath,
    SearchPathFilterFunc,
    Tool,
    file_allowed,
    tool_name,
)
from .binary import (
    BinaryReadResult,
    ReadBinaryDocumentTool,
)
from .documents import (
    DocumentRange,
    DocumentSummary,
    DocumentTreeNode,
    GlobDocumentsTool,
    ListDocumentsTool,
    ReadDocumentTool,
)
from .grep import GrepLine, GrepMatch, GrepTool
from .jq import JqTool
from .mutations import EditDocumentTool, WriteDocumentTool
from .plan import CreatePlanTool
from .retrieval import SearchResult, SearchType, VectorSearchTool
from .scope import Scope
from .web import WebFetch, WebSearch

__all__ = [
    "DEFAULT_EXCLUDE_DIRS",
    "BinaryAttachment",
    "BinaryReadResult",
    "CreatePlanTool",
    "DocumentRange",
    "DocumentSummary",
    "DocumentTreeNode",
    "EditDocumentTool",
    "GlobDocumentsTool",
    "GrepLine",
    "GrepMatch",
    "GrepTool",
    "JqTool",
    "ListDocumentsTool",
    "ReadBinaryDocumentTool",
    "ReadDocumentTool",
    "Scope",
    "SearchPath",
    "SearchPathFilterFunc",
    "SearchResult",
    "SearchType",
    "Tool",
    "VectorSearchTool",
    "WebFetch",
    "WebSearch",
    "WriteDocumentTool",
    "file_allowed",
    "tool_name",
]
