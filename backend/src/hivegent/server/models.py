"""Request models shared across the server routes."""

from pydantic import BaseModel, Field

from ..chunkers import ChunkingSpec
from ..converters import ConversionSpec
from ..types import LlmConfig

__all__ = [
    "BulkDeleteRequest",
    "BulkRechunkRequest",
    "BulkReconvertRequest",
    "PipelineSpec",
    "ReconvertRequest",
]


class PipelineSpec(BaseModel):
    """Bundled conversion and chunking pipeline selection."""

    conversion: ConversionSpec = Field(default_factory=ConversionSpec)
    chunking: ChunkingSpec = Field(default_factory=ChunkingSpec)


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
