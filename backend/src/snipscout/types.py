"""Shared types for the RAG application."""

from pydantic import BaseModel, Field

__all__ = [
    "CreateConversationResponse",
    "DocumentRange",
    "DocumentStats",
    "GrepMatch",
    "RetrievedDocument",
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
