"""Read-only and SSE-streaming helpers for document routes.

All workspace, metadata, and search-index *mutations* live in
:mod:`hivegent.workspace`.  This package contains only:

- read-only filesystem helpers (:mod:`.reads`, :mod:`.inventory`)
- SSE event wrappers around workspace mutations (:mod:`.streaming`)
"""

from .inventory import build_tree_response, list_documents_for_store
from .reads import find_original, get_document_response, list_assets
from .streaming import (
    PreparedCollection,
    prepare_collection_upload,
    process_bulk_operation,
    read_collection_zip,
    reconvert_single_stream,
    upload_file_stream,
    validate_collection_upload,
)

__all__ = [
    "PreparedCollection",
    "build_tree_response",
    "find_original",
    "get_document_response",
    "list_assets",
    "list_documents_for_store",
    "prepare_collection_upload",
    "process_bulk_operation",
    "read_collection_zip",
    "reconvert_single_stream",
    "upload_file_stream",
    "validate_collection_upload",
]
