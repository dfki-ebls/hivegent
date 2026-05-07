"""Request models shared across the server routes."""

from pydantic import BaseModel, Field

from ..types import LlmConfig, PipelineSpec

__all__ = [
    "BulkDeleteRequest",
    "BulkRechunkRequest",
    "BulkReconvertRequest",
    "ReconvertRequest",
]


class ReconvertRequest(BaseModel):
    """Request to reconvert a document from its original binary file."""

    pipeline: PipelineSpec = Field(default_factory=PipelineSpec)
    llm: LlmConfig = Field(default_factory=LlmConfig)


class BulkRechunkRequest(BaseModel):
    """Request to bulk rechunk multiple documents."""

    files: list[str] = Field(description="List of file paths to rechunk")
    pipeline: PipelineSpec = Field(default_factory=PipelineSpec)


class BulkReconvertRequest(BaseModel):
    """Request to bulk reconvert multiple documents from originals."""

    files: list[str] = Field(description="List of file paths to reconvert")
    pipeline: PipelineSpec = Field(default_factory=PipelineSpec)
    llm: LlmConfig = Field(default_factory=LlmConfig)


class BulkDeleteRequest(BaseModel):
    """Request to bulk delete multiple documents."""

    files: list[str] = Field(description="List of file paths to delete")
