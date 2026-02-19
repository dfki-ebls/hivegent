"""Shared types for the RAG application."""

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, Field


@dataclass(slots=True, frozen=True)
class User:
    """Authenticated user information."""

    id: str
    email: str | None = None
    name: str | None = None


class Personality(StrEnum):
    """Available assistant personalities."""

    DEFAULT = "default"
    CONCISE = "concise"
    DETAILED = "detailed"


class LlmConfig(BaseModel):
    """Client-provided LLM configuration overrides."""

    model: str = ""
    api_key: str = ""
    base_url: str | None = None


@dataclass(slots=True, frozen=True)
class DocumentFilter:
    """Include/exclude filter applied to document-level tool operations.

    If ``included`` is non-empty the filepath must match an entry.
    If ``excluded`` is non-empty the filepath must *not* match.
    When both are set, ``included`` is checked first.

    Entries ending with ``/`` are treated as directory prefixes:
    ``"projects/"`` matches ``"projects/report.md"`` and
    ``"projects/sub/file.txt"``.
    Entries without a trailing ``/`` are exact file matches.
    """

    included: frozenset[str] = field(default_factory=frozenset)
    excluded: frozenset[str] = field(default_factory=frozenset)

    @staticmethod
    def _matches(entry: str, filepath: str) -> bool:
        """Check if a filter entry matches a filepath."""
        if entry.endswith("/"):
            return filepath.startswith(entry)
        return filepath == entry

    def is_included(self, filepath: str) -> bool:
        """Return whether *filepath* passes the filter."""
        if self.included and not any(
            self._matches(entry, filepath) for entry in self.included
        ):
            return False
        if self.excluded and any(
            self._matches(entry, filepath) for entry in self.excluded
        ):
            return False
        return True


__all__ = [
    "ChatRequestConfig",
    "ChunkedDocument",
    "ChunkInfo",
    "ChunkSummary",
    "CollectionUploadResponse",
    "CompactConversationResponse",
    "ConversationData",
    "ConversationListResponse",
    "ConversationSummary",
    "CreateConversationResponse",
    "CreateDirectoryRequest",
    "CreateDirectoryResponse",
    "CreateTokenRequest",
    "CreateTokenResponse",
    "DeleteConversationResponse",
    "DeleteDirectoryResponse",
    "DeleteDocumentResponse",
    "DirectoryEntry",
    "DocumentFilter",
    "DirectoryTreeResponse",
    "DocumentInfo",
    "DocumentListResponse",
    "DocumentRange",
    "DocumentReference",
    "DocumentSummary",
    "GenerateTitleRequest",
    "GenerateTitleResponse",
    "GrepMatch",
    "LlmConfig",
    "MoveDocumentRequest",
    "MoveDocumentResponse",
    "Personality",
    "RetrievedChunk",
    "SettingsResponse",
    "TokenInfo",
    "UpdateTitleRequest",
    "UploadDocumentResponse",
    "User",
]


class ChatRequestConfig(BaseModel):
    """Configuration for chat requests, passed via request body."""

    conversation_id: str = Field(default="", description="The conversation ID")
    personality: Personality = Field(default=Personality.DEFAULT)
    llm: LlmConfig = Field(default_factory=LlmConfig)
    included_documents: list[str] = Field(default_factory=list)
    excluded_documents: list[str] = Field(default_factory=list)


class CreateConversationResponse(BaseModel):
    """Response for conversation creation."""

    id: str = Field(description="The unique conversation ID")


class DocumentRange(BaseModel):
    """A range of lines from a document."""

    start_line: int
    end_line: int
    total_lines: int
    content: str


class DocumentSummary(BaseModel):
    """Summary of a document."""

    filename: str
    size: int


class GrepMatch(BaseModel):
    """A pattern match in a document."""

    filename: str = Field(description="The filename containing the match")
    line: int = Field(description="Line number of the match (1-indexed)")
    content: str | None = Field(default=None, description="The matching line content")


class ChunkInfo(BaseModel):
    """A single chunk within a chunked document."""

    text: str = Field(description="The chunk text content")
    token_count: int = Field(description="Number of tokens in the chunk")
    start_index: int = Field(description="Start character index in original document")
    end_index: int = Field(description="End character index in original document")
    index: int = Field(description="Chunk index within the document")


class ChunkedDocument(BaseModel):
    """A document that has been chunked, with metadata."""

    chunking_pipeline: str = Field(description="The chunking pipeline used")
    chunk_size: int = Field(description="The target chunk size in tokens")
    created_at: datetime = Field(description="When the chunks were created")
    chunk_count: int = Field(description="Total number of chunks")
    chunks: list[ChunkInfo] = Field(description="The document chunks")


class ChunkSummary(BaseModel):
    """Summary metadata for a single chunk (used by agent tools)."""

    index: int = Field(description="Chunk index within the document")
    token_count: int = Field(description="Number of tokens in the chunk")
    start_index: int = Field(description="Start character index in original document")
    end_index: int = Field(description="End character index in original document")


class RetrievedChunk(BaseModel):
    """A chunk retrieved from search."""

    filename: str = Field(description="The document filename")
    chunk_index: int = Field(description="Chunk index within the document")
    text: str = Field(description="The chunk text content")
    token_count: int = Field(description="Number of tokens in the chunk")
    score: float = Field(description="The relevance score from BM25")


class DocumentInfo(BaseModel):
    """Metadata about a document in the data directory."""

    filename: str = Field(description="The filename of the document")
    size_bytes: int = Field(description="File size in bytes")
    modified_at: datetime = Field(description="Last modification timestamp")
    chunk_count: int | None = Field(
        default=None, description="Number of chunks, if chunked"
    )
    has_original: bool = Field(
        default=False,
        description="Whether an original binary file exists for reconversion",
    )


class DocumentListResponse(BaseModel):
    """Response for listing documents."""

    documents: list[DocumentInfo] = Field(description="List of document metadata")
    total_count: int = Field(description="Total number of documents")


class UploadDocumentResponse(BaseModel):
    """Response for document upload."""

    filename: str = Field(description="The uploaded filename")
    converted_filename: str | None = Field(
        default=None, description="The converted markdown filename (for binary files)"
    )
    size_bytes: int = Field(description="File size in bytes")
    conversion_pipeline_used: str | None = Field(
        default=None, description="The conversion pipeline used (for binary files)"
    )
    chunk_count: int | None = Field(
        default=None, description="Number of chunks created"
    )
    chunking_pipeline_used: str | None = Field(
        default=None, description="The chunking pipeline used"
    )
    message: str = Field(description="Status message")


class DeleteDocumentResponse(BaseModel):
    """Response for document deletion."""

    filename: str = Field(description="The deleted filename")
    message: str = Field(description="Status message")


class DocumentReference(BaseModel):
    """A reference to a document accessed during a conversation."""

    filename: str = Field(description="The filename of the document")
    sources: list[str] = Field(description="How the document was accessed")
    score: float | None = Field(default=None, description="Search relevance score")


class ConversationData(BaseModel):
    """Full conversation data including messages and metadata."""

    id: str = Field(description="Unique conversation ID")
    title: str = Field(description="Conversation title")
    created_at: datetime = Field(description="When the conversation was created")
    updated_at: datetime = Field(description="When the conversation was last updated")
    document_references: list[DocumentReference] = Field(
        default_factory=list, description="Documents accessed during the conversation"
    )
    messages: list[dict[str, Any]] = Field(
        default_factory=list, description="Conversation messages"
    )
    compacted_from: str | None = Field(
        default=None,
        description="ID of the conversation this was compacted from",
    )


class ConversationSummary(BaseModel):
    """Summary information for listing conversations."""

    id: str = Field(description="Unique conversation ID")
    title: str = Field(description="Conversation title")
    created_at: datetime = Field(description="When the conversation was created")
    updated_at: datetime = Field(description="When the conversation was last updated")
    message_count: int = Field(description="Number of messages in the conversation")
    compacted_from: str | None = Field(
        default=None,
        description="ID of the conversation this was compacted from",
    )


class ConversationListResponse(BaseModel):
    """Response for listing conversations."""

    conversations: list[ConversationSummary] = Field(
        description="List of conversation summaries"
    )
    total_count: int = Field(description="Total number of conversations")


class CompactConversationResponse(BaseModel):
    """Response for conversation compaction."""

    new_conversation_id: str = Field(description="ID of the new compacted conversation")
    summary: str = Field(description="Summary of the original conversation")
    message: str = Field(description="Status message")


class DeleteConversationResponse(BaseModel):
    """Response for conversation deletion."""

    id: str = Field(description="The deleted conversation ID")
    message: str = Field(description="Status message")


class UpdateTitleRequest(BaseModel):
    """Request to update a conversation title."""

    title: str = Field(description="The new title for the conversation")


class SettingsResponse(BaseModel):
    """LLM settings (API key masked as boolean)."""

    model: str = Field(description="Default chat model")
    vision_model: str = Field(description="Default vision model for conversion")
    small_model: str = Field(description="Default model for lightweight tasks")
    has_api_key: bool = Field(description="Whether a server-side API key is configured")
    base_url: str = Field(description="Default base URL for the LLM provider")


class GenerateTitleRequest(BaseModel):
    """Request to generate a title using an LLM."""

    llm: LlmConfig = Field(default_factory=LlmConfig)


class GenerateTitleResponse(BaseModel):
    """Response from title generation."""

    title: str = Field(description="The generated title")


class CreateTokenRequest(BaseModel):
    """Request to create a personal access token."""

    name: str = Field(description="A name for the token")
    expires_in_days: int | None = Field(
        default=None, description="Optional expiration in days"
    )


class CreateTokenResponse(BaseModel):
    """Response from token creation."""

    token: str = Field(description="The raw token (only shown once)")
    id: str = Field(description="The token ID for management")
    name: str = Field(description="The token name")
    created_at: datetime = Field(description="When the token was created")
    expires_at: datetime | None = Field(
        default=None, description="When the token expires"
    )


class TokenInfo(BaseModel):
    """Information about a personal access token (without the token value)."""

    id: str = Field(description="The token ID")
    name: str = Field(description="The token name")
    created_at: datetime = Field(description="When the token was created")
    expires_at: datetime | None = Field(
        default=None, description="When the token expires"
    )
    last_used_at: datetime | None = Field(
        default=None, description="When the token was last used"
    )


class DirectoryEntry(BaseModel):
    """A file or directory entry in the document tree."""

    type: Literal["file", "directory"]
    name: str = Field(description="Basename of the entry")
    path: str = Field(description="Relative path from documents root")
    size_bytes: int | None = Field(default=None, description="File size in bytes")
    modified_at: datetime | None = Field(
        default=None, description="Last modification timestamp"
    )
    chunk_count: int | None = Field(
        default=None, description="Number of chunks, if chunked"
    )
    has_original: bool = Field(
        default=False, description="Whether an original binary file exists"
    )
    children: list["DirectoryEntry"] | None = Field(
        default=None, description="Child entries for directories"
    )


class DirectoryTreeResponse(BaseModel):
    """Response for the directory tree endpoint."""

    root: DirectoryEntry
    total_files: int = Field(description="Total number of files")
    total_directories: int = Field(description="Total number of directories")


class CreateDirectoryRequest(BaseModel):
    """Request to create a new directory."""

    path: str = Field(description="Relative path of the directory to create")


class CreateDirectoryResponse(BaseModel):
    """Response for directory creation."""

    path: str = Field(description="The created directory path")
    message: str = Field(description="Status message")


class MoveDocumentRequest(BaseModel):
    """Request to move a document to a new location."""

    destination: str = Field(description="Destination relative path")


class MoveDocumentResponse(BaseModel):
    """Response for document move."""

    source: str = Field(description="Original relative path")
    destination: str = Field(description="New relative path")
    message: str = Field(description="Status message")


class DeleteDirectoryResponse(BaseModel):
    """Response for directory deletion."""

    path: str = Field(description="The deleted directory path")
    files_deleted: int = Field(description="Number of files deleted")
    message: str = Field(description="Status message")


class CollectionUploadResponse(BaseModel):
    """Response for collection (directory/ZIP) upload."""

    total_files: int = Field(description="Total files processed")
    markdown_files: int = Field(description="Number of markdown files uploaded")
    converted_attachments: int = Field(
        description="Number of binary attachments converted"
    )
    failed_files: list[str] = Field(
        default_factory=list, description="Files that failed to process"
    )
    message: str = Field(description="Status message")
