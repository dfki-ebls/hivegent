"""Shared types for the RAG application."""

from datetime import datetime

from pydantic import BaseModel, Field

__all__ = [
    "CreateConversationResponse",
    "DeleteDocumentResponse",
    "DocumentInfo",
    "DocumentListResponse",
    "DocumentRange",
    "DocumentStats",
    "GrepMatch",
    "RetrievedDocument",
    "UploadDocumentResponse",
]


class CreateConversationResponse(BaseModel):
    """Response for conversation creation."""

    id: str = Field(description="The unique conversation ID")


class DocumentStats(BaseModel):
    """Statistics about a document."""

    line_count: int
    word_count: int
    char_count: int


class DocumentRange(BaseModel):
    """A range of lines from a document."""

    start_line: int
    end_line: int
    content: str


class GrepMatch(BaseModel):
    """A pattern match in a document."""

    filename: str = Field(description="The filename containing the match")
    line_number: int = Field(description="Line number of the match (1-indexed)")
    line: str = Field(description="The matching line content")


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
