"""Shared document operations exposed as a small package."""

from .collections import (
    process_collection,
    read_collection_zip,
    validate_collection_upload,
)
from .files import (
    delete_directory_internal,
    delete_single,
    ensure_upload_slot,
    find_original,
    get_document_response,
    list_assets,
    move_directory_internal,
    move_document_internal,
    update_asset_description,
)
from .inventory import build_tree_response, list_documents_for_store
from .streaming import process_bulk_operation
from .uploads import (
    reconvert_single,
    reconvert_single_stream,
    upload_file,
    upload_file_stream,
)

__all__ = [
    "build_tree_response",
    "delete_directory_internal",
    "delete_single",
    "ensure_upload_slot",
    "find_original",
    "get_document_response",
    "list_assets",
    "list_documents_for_store",
    "move_directory_internal",
    "move_document_internal",
    "process_bulk_operation",
    "process_collection",
    "read_collection_zip",
    "reconvert_single",
    "reconvert_single_stream",
    "update_asset_description",
    "upload_file",
    "upload_file_stream",
    "validate_collection_upload",
]
