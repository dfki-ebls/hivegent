"""Tools subpackage — self-contained, framework-free tool implementations."""

from .base import (
    DEFAULT_EXCLUDE_DIRS,
    Batch,
    BinaryAttachment,
    ItemFailure,
    SearchPath,
    SearchPathFilterFunc,
    Tool,
    file_allowed,
    tool_name,
)
from .binary import (
    BinaryRead,
    BinaryReadResult,
    ReadBinaryDocumentTool,
)
from .documents import (
    DocumentRange,
    DocumentRead,
    DocumentSummary,
    DocumentTreeNode,
    GlobDocumentsTool,
    ListDocumentsTool,
    ReadDocumentTool,
)
from .grep import GrepLine, GrepMatch, GrepTool
from .jq import JqResult, JqTool
from .mutations import (
    DeleteDocumentsTool,
    DocumentMove,
    EditDocumentTool,
    MoveDocumentsTool,
    WriteDocumentTool,
)
from .python import PythonResult, RunPythonTool
from .retrieval import SearchResult, SearchType, VectorSearchTool
from .scope import Scope
from .sink import RedirectedOutput
from .table import QueryTableTool, TableResult
from .web import (
    WebFetch,
    WebPage,
    WebSearch,
    WebSearchHit,
    WebSearchResults,
    WikipediaSearch,
    build_user_agent,
)

__all__ = [
    "DEFAULT_EXCLUDE_DIRS",
    "Batch",
    "BinaryAttachment",
    "BinaryRead",
    "BinaryReadResult",
    "DeleteDocumentsTool",
    "DocumentMove",
    "DocumentRange",
    "DocumentRead",
    "DocumentSummary",
    "DocumentTreeNode",
    "EditDocumentTool",
    "GlobDocumentsTool",
    "GrepLine",
    "GrepMatch",
    "GrepTool",
    "ItemFailure",
    "JqResult",
    "JqTool",
    "ListDocumentsTool",
    "MoveDocumentsTool",
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
    "WebSearchHit",
    "WebSearchResults",
    "WikipediaSearch",
    "WriteDocumentTool",
    "build_user_agent",
    "file_allowed",
    "tool_name",
]
