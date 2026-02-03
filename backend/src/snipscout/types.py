"""Shared types for the RAG application."""

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field


class Personality(StrEnum):
    """Available assistant personalities."""

    DEFAULT = "default"
    CONCISE = "concise"
    DETAILED = "detailed"


__all__ = [
    "ChatRequestConfig",
    "ConversationData",
    "ConversationListResponse",
    "ConversationSummary",
    "CreateConversationResponse",
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
    "RetrievedDocument",
    "UpdateTitleRequest",
    "UploadDocumentResponse",
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


class DocumentInfo(BaseModel):
    """Metadata about a document in the data directory."""

    filename: str = Field(description="The filename of the document")
    size_bytes: int = Field(description="File size in bytes")
    modified_at: datetime = Field(description="Last modification timestamp")


class DocumentListResponse(BaseModel):
    """Response for listing documents."""

    documents: list[DocumentInfo] = Field(description="List of document metadata")
    total_count: int = Field(description="Total number of documents")


class UploadDocumentResponse(BaseModel):
    """Response for document upload."""

    filename: str = Field(description="The uploaded filename")
    size_bytes: int = Field(description="File size in bytes")
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
