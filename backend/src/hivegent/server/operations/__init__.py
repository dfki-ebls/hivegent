"""Read-only and job helpers for document routes.

All workspace, metadata, and search-index *mutations* live in
:mod:`hivegent.workspace`.  This package contains only:

- read-only filesystem helpers (:mod:`.reads`, :mod:`.inventory`)
- upload guarding and the bulk-job runner shared by the routes (:mod:`.processing`)
"""

from .inventory import build_tree_response
from .processing import (
    cleanup_spool_dir,
    enforce_upload_size,
    run_bulk_document_job,
    spool_dir,
    summarize_failed_files,
    summarize_failures,
    validate_collection_upload,
)
from .reads import (
    attachment_disposition,
    find_original,
    get_document_response,
    list_assets,
)

__all__ = [
    "attachment_disposition",
    "build_tree_response",
    "cleanup_spool_dir",
    "enforce_upload_size",
    "find_original",
    "get_document_response",
    "list_assets",
    "run_bulk_document_job",
    "spool_dir",
    "summarize_failed_files",
    "summarize_failures",
    "validate_collection_upload",
]
