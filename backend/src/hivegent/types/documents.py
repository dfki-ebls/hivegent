"""API models of documents, directories, assets, and workspace processing."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from ..metadata import DocumentMetadata

__all__ = [
    "AssetEntry",
    "AssetListResponse",
    "CollectionCompleteEvent",
    "CollectionProgressEvent",
    "DirectoryEntry",
    "DirectoryTreeResponse",
    "DocumentChunksResponse",
    "DocumentLineCountsResponse",
    "FailedFile",
    "GenerateAssetDescriptionRequest",
    "TmpClearedResponse",
    "UpdateAssetDescriptionRequest",
    "WriteDocumentRequest",
]


class AssetEntry(BaseModel):
    """Information about a single file in a document's assets directory."""

    name: str = Field(description="Path relative to the assets directory")
    path: str = Field(description="Workspace-relative path to the asset file")
    description_path: str | None = Field(
        default=None,
        description="Workspace-relative path to the companion .md description file",
    )
    description: str = Field(
        default="",
        description="Content of the companion .md file",
    )
    size_bytes: int = Field(description="File size in bytes")
    media_type: str | None = Field(
        default=None,
        description="MIME type of the asset file",
    )


class AssetListResponse(BaseModel):
    """Response for listing a document's assets."""

    assets: list[AssetEntry] = Field(description="Asset files in the directory")
    assets_dir: str = Field(description="Workspace-relative assets directory path")


class UpdateAssetDescriptionRequest(BaseModel):
    """Request to update an asset's companion .md description."""

    asset_name: str = Field(description="Path of the asset within its assets directory")
    content: str = Field(description="New content for the .md description file")


class GenerateAssetDescriptionRequest(BaseModel):
    """Request to generate an asset's companion .md description with the vision model."""

    asset_name: str = Field(description="Path of the asset within its assets directory")


class DocumentLineCountsResponse(BaseModel):
    """Batch document line counts, keyed by the requested workspace path."""

    line_counts: dict[str, int] = Field(
        description="Requested path → line count; unknown paths are omitted"
    )


class WriteDocumentRequest(BaseModel):
    """Request to write a text document, replacing it or creating it fresh."""

    content: str = Field(description="New full content for the document")
    mode: Literal["replace", "create"] = Field(
        default="replace",
        description="'replace' overwrites or creates; 'create' rejects an existing path",
    )


class DirectoryEntry(BaseModel):
    """A logical file or directory entry in the document tree."""

    type: Literal["file", "directory"]
    name: str = Field(description="Basename of the entry")
    path: str = Field(description="Relative path from documents root")
    size_bytes: int | None = Field(default=None, description="File size in bytes")
    modified_at: datetime | None = Field(
        default=None,
        description="Last modification timestamp",
    )
    chunk_count: int | None = Field(
        default=None,
        description="Number of chunks, if chunked",
    )
    has_original: bool = Field(
        default=False,
        description="Whether an original binary file exists",
    )
    original_path: str | None = Field(
        default=None,
        description="Workspace-relative original file path when present",
    )
    assets_dir: str | None = Field(
        default=None,
        description="Workspace-relative child-assets directory when present",
    )
    case_base_candidate: bool = Field(
        default=False,
        description="Whether it may be a case base by cbrkit's rule, which offers its similarity",
    )
    children: list[DirectoryEntry] | None = Field(
        default=None,
        description="Child entries for directories",
    )


class DocumentChunksResponse(DocumentMetadata):
    """A processed entry with whether its original may be a case base."""

    case_base_candidate: bool = Field(
        default=False,
        description="Whether its original may be a case base by cbrkit's rule, which offers its similarity",
    )


class DirectoryTreeResponse(BaseModel):
    """Response for the directory tree endpoint."""

    root: DirectoryEntry
    total_files: int = Field(description="Total number of files")
    total_directories: int = Field(description="Total number of directories")


class FailedFile(BaseModel):
    """A collection member that was skipped or failed, with a short reason."""

    path: str = Field(description="Workspace-relative path of the file")
    reason: str = Field(description="Why the file was not imported")


class CollectionProgressEvent(BaseModel):
    """Progress event yielded per file while a collection job runs."""

    current: int = Field(description="Number of files processed so far")
    total: int = Field(description="Total number of files to process")


class CollectionCompleteEvent(BaseModel):
    """Terminal event summarizing a finished collection import."""

    markdown_files: int = Field(description="Number of markdown files uploaded")
    converted_attachments: int = Field(
        description="Number of binary attachments converted",
    )
    failed_files: list[FailedFile] = Field(
        default_factory=list,
        description="Files that were skipped or failed, each with its reason",
    )
    message: str = Field(description="Status message")


class TmpClearedResponse(BaseModel):
    """Result of clearing the ``/tmp`` folders of the caller's conversations."""

    files_removed: int = Field(description="Number of temporary files deleted")
