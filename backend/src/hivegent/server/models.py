"""Request models shared across the server routes."""

from collections.abc import Callable
from typing import Annotated, Literal

from pydantic import BaseModel, Field

from ..changes import CreateDir, Delete, DeleteKind, Move
from ..llm_config import LlmConfig
from ..types import PipelineSpec

__all__ = [
    "MAX_CHANGE_OPERATIONS",
    "BulkRechunkRequest",
    "BulkReconvertRequest",
    "ChangeOperation",
    "ChangesRequest",
    "CreateDirOperation",
    "DeleteOperation",
    "DocumentLineCountsRequest",
    "MoveDestination",
    "MoveOperation",
    "MovePaths",
    "ReconvertRequest",
    "WorkspacePath",
]

MAX_CHANGE_OPERATIONS = 10_000
"""Most operations one change request carries, as many as a collection may import."""


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


class DocumentLineCountsRequest(BaseModel):
    """Request a batch of document line counts by workspace path."""

    files: list[str] = Field(description="Workspace paths to look up line counts for")


class MoveDestination(BaseModel):
    """Where a moved document or directory ends up."""

    destination: str = Field(
        description="Canonical path it ends up at, or an existing directory to move into"
    )


class MovePaths(MoveDestination):
    """A document or directory and where it ends up."""

    source: str = Field(description="Canonical path of the document or directory")


class MoveOperation(MovePaths):
    """Move or rename a document or directory, into another workspace too."""

    kind: Literal["move"]

    def operation[L](self, at: Callable[[str], L]) -> Move[L]:
        """The gateway's move, with each path resolved by *at*."""
        return Move(at(self.source), at(self.destination))


class WorkspacePath(BaseModel):
    """One canonical workspace path."""

    path: str = Field(description="Canonical path of the document or directory")


class DeleteOperation(WorkspacePath):
    """Delete a document with everything derived from it, or a whole directory."""

    kind: Literal["delete"]
    expect: DeleteKind = Field(
        description="What the path has to be, refused when it is the other kind"
    )

    def operation[L](self, at: Callable[[str], L]) -> Delete[L]:
        """The gateway's delete, with the path resolved by *at*."""
        return Delete(at(self.path), expect=self.expect)


class CreateDirOperation(WorkspacePath):
    """Create an empty directory."""

    kind: Literal["mkdir"]

    def operation[L](self, at: Callable[[str], L]) -> CreateDir[L]:
        """The gateway's new directory, with the path resolved by *at*."""
        return CreateDir(at(self.path))


type ChangeOperation = Annotated[
    MoveOperation | DeleteOperation | CreateDirOperation, Field(discriminator="kind")
]
"""One item of a :class:`ChangesRequest`."""


class ChangesRequest(BaseModel):
    """Workspace changes that apply as one changeset, all of them or none."""

    operations: list[ChangeOperation] = Field(
        min_length=1, max_length=MAX_CHANGE_OPERATIONS
    )
