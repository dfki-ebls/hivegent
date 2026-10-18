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
from .table import QueryTableTool, TableResult
from .web import (
    KnownUrls,
    OpenserpBackend,
    SearchBackend,
    SearxngBackend,
    WebFetch,
    WebPage,
    WebQuery,
    WebSearch,
    WebSearchHit,
    WebSearchResults,
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
    "EditDocumentTool",
    "GrepLine",
    "GrepMatch",
    "GrepTool",
    "ItemFailure",
    "JqResult",
    "JqTool",
    "KnownUrls",
    "ListDocumentsTool",
    "MoveDocumentsTool",
    "OpenserpBackend",
    "PythonResult",
    "QueryTableTool",
    "ReadBinaryDocumentTool",
    "ReadDocumentTool",
    "RunPythonTool",
    "Scope",
    "SearchBackend",
    "SearchPath",
    "SearchPathFilterFunc",
    "SearchResult",
    "SearchType",
    "SearxngBackend",
    "TableResult",
    "Tool",
    "VectorSearchTool",
    "WebFetch",
    "WebPage",
    "WebQuery",
    "WebSearch",
    "WebSearchHit",
    "WebSearchResults",
    "WriteDocumentTool",
    "build_user_agent",
    "file_allowed",
    "tool_name",
]
