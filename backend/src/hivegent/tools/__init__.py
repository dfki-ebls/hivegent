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
from .jq import JqResult, JqTool
from .mutations import EditDocumentTool, WriteDocumentTool
from .plan import CreatePlanTool
from .python import PythonResult, RunPythonTool
from .retrieval import SearchResult, SearchType, VectorSearchTool
from .scope import Scope
from .sink import RedirectedOutput
from .table import QueryTableTool, TableResult
from .web import WebFetch, WebPage, WebSearch, build_user_agent

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
    "JqResult",
    "JqTool",
    "ListDocumentsTool",
    "PythonResult",
    "QueryTableTool",
    "ReadBinaryDocumentTool",
    "ReadDocumentTool",
    "RedirectedOutput",
    "RunPythonTool",
    "Scope",
    "SearchPath",
    "SearchPathFilterFunc",
    "SearchResult",
    "SearchType",
    "TableResult",
    "Tool",
    "VectorSearchTool",
    "WebFetch",
    "WebPage",
    "WebSearch",
    "WriteDocumentTool",
    "build_user_agent",
    "file_allowed",
    "tool_name",
]
