"""Shared types for the RAG application."""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field


@dataclass
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


__all__ = [
    "ChatRequestConfig",
    "ChunkSummary",
    "ConversationData",
    "ConversationListResponse",
    "ConversationSummary",
    "CreateConversationResponse",
    "CreateTokenRequest",
    "CreateTokenResponse",
    "DeleteConversationResponse",
    "DeleteDocumentResponse",
    "DocumentInfo",
    "DocumentListResponse",
    "DocumentRange",
    "DocumentReference",
    "DocumentSummary",
    "GenerateTitleRequest",
    "GenerateTitleResponse",
    "GrepMatch",
    "Personality",
    "RetrievedChunk",
    "RetrievedDocument",
    "TokenInfo",
    "UpdateTitleRequest",
    "UploadDocumentResponse",
    "User",
]


class ChatRequestConfig(BaseModel):
    """Configuration for chat requests, passed via HTTP headers."""

    conversation_id: str = Field(description="The conversation ID for persistence")
    model: str = Field(description="Model identifier (e.g., 'openai/gpt-4o')")
    api_key: str = Field(description="API key for the LLM provider")
    base_url: str | None = Field(default=None, description="Custom base URL for the LLM provider")
    personality: Personality = Field(default=Personality.DEFAULT, description="Assistant personality")


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


class RetrievedDocument(BaseModel):
    """A document retrieved from search."""

    filename: str = Field(description="The filename of the document")
    content: str = Field(description="The full content of the document")
    score: float = Field(description="The relevance score from BM25")


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
    chunk_count: int | None = Field(default=None, description="Number of chunks, if chunked")
    has_original: bool = Field(default=False, description="Whether an original binary file exists for reconversion")


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
    messages: list[dict] = Field(
        default_factory=list, description="Conversation messages"
    )


class ConversationSummary(BaseModel):
    """Summary information for listing conversations."""

    id: str = Field(description="Unique conversation ID")
    title: str = Field(description="Conversation title")
    created_at: datetime = Field(description="When the conversation was created")
    updated_at: datetime = Field(description="When the conversation was last updated")
    message_count: int = Field(description="Number of messages in the conversation")


class ConversationListResponse(BaseModel):
    """Response for listing conversations."""

    conversations: list[ConversationSummary] = Field(
        description="List of conversation summaries"
    )
    total_count: int = Field(description="Total number of conversations")


class DeleteConversationResponse(BaseModel):
    """Response for conversation deletion."""

    id: str = Field(description="The deleted conversation ID")
    message: str = Field(description="Status message")


class UpdateTitleRequest(BaseModel):
    """Request to update a conversation title."""

    title: str = Field(description="The new title for the conversation")


class GenerateTitleRequest(BaseModel):
    """Request to generate a title using an LLM."""

    model: str = Field(description="Model identifier")
    api_key: str = Field(default="", description="API key for the LLM provider")
    base_url: str | None = Field(default=None, description="Base URL for the LLM API")


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


