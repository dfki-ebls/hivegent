"""Document mutation tool callables — edit and write."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import Annotated, Literal, override

from fastapi import HTTPException
from pydantic import Field

from .base import AsyncPathTool, ToolOutput, resolve_accessible_file
from .documents import DocumentFilePathArg

__all__ = [
    "DocumentContentArg",
    "EditDocumentTool",
    "EditNewStringArg",
    "EditOldStringArg",
    "EditReplaceAllArg",
    "EditMutation",
    "WriteDocumentTool",
    "WriteMutation",
    "WriteModeArg",
]

EditOldStringArg = Annotated[
    str,
    Field(
        description=(
            "Exact text to replace.  Must occur exactly once unless "
            "`replace_all` is true."
        ),
    ),
]
EditNewStringArg = Annotated[
    str,
    Field(description="Replacement text."),
]
EditReplaceAllArg = Annotated[
    bool,
    Field(
        description=(
            "When true, replace every occurrence of `old_string` instead "
            "of requiring a unique match.  Use this for renames and "
            "global substitutions."
        ),
    ),
]
DocumentContentArg = Annotated[
    str,
    Field(description="Text content to write to the document."),
]
WriteModeArg = Annotated[
    Literal["prepend", "append", "replace"],
    Field(
        description=(
            "Write mode: `replace` overwrites or creates the file, `append` "
            "adds to the end, and `prepend` adds to the start."
        ),
    ),
]

EditMutation = Callable[[str, str, str, bool], Awaitable[str]]
"""Canonical edit operation for a resolved local document path."""

WriteMutation = Callable[[str, str, WriteModeArg], Awaitable[str]]
"""Canonical write operation for a resolved local document path."""


def _mutation_error(exc: HTTPException | ValueError) -> ToolOutput[str]:
    """Render a failed mutation as a tool error message."""
    detail = exc.detail if isinstance(exc, HTTPException) else str(exc)
    return ToolOutput(data=f"Error: {detail}")


@dataclass(slots=True, frozen=True)
class EditDocumentTool(AsyncPathTool[str]):
    """Edit a document by replacing an exact string with a new string.

    Resolves and access-checks the path, then delegates the mutation to
    :attr:`mutator` — the canonical workspace gateway that owns the
    string-replacement semantics and re-indexing.
    """

    mutator: EditMutation = field(kw_only=True)

    @override
    async def __call__(
        self,
        file_path: DocumentFilePathArg,
        old_string: EditOldStringArg,
        new_string: EditNewStringArg,
        replace_all: EditReplaceAllArg = False,
    ) -> ToolOutput[str]:
        """Replace an exact string in a document.

        By default the match must be unique — fails if ``old_string`` does
        not exist or appears more than once.  Pass ``replace_all=True`` to
        substitute every occurrence instead.
        """
        resolved = resolve_accessible_file(self.resolved_paths, file_path)
        if resolved is None:
            return ToolOutput(data=f"Error: '{file_path}' is not accessible.")
        _sp, local, _absolute = resolved
        try:
            data = await self.mutator(local, old_string, new_string, replace_all)
        except (HTTPException, ValueError) as exc:
            return _mutation_error(exc)
        return ToolOutput(data=data)


@dataclass(slots=True, frozen=True)
class WriteDocumentTool(AsyncPathTool[str]):
    """Write content to a document using prepend, append, or replace mode.

    Resolves and access-checks the path (optionally enforcing :attr:`glob`),
    then delegates the mutation to :attr:`mutator` — the canonical workspace
    gateway that owns the write-mode semantics and re-indexing.
    """

    glob: str | None = None
    mutator: WriteMutation = field(kw_only=True)

    @override
    async def __call__(
        self,
        file_path: DocumentFilePathArg,
        content: DocumentContentArg,
        mode: WriteModeArg = "replace",
    ) -> ToolOutput[str]:
        """Write content to a document."""
        resolved = resolve_accessible_file(self.resolved_paths, file_path)
        if resolved is None:
            return ToolOutput(data=f"Error: '{file_path}' is not accessible.")
        _sp, local, _absolute = resolved
        if self.glob and not PurePosixPath(local).match(self.glob):
            return ToolOutput(
                data=f"Error: '{file_path}' does not match pattern '{self.glob}'.",
            )
        try:
            data = await self.mutator(local, content, mode)
        except (HTTPException, ValueError) as exc:
            return _mutation_error(exc)
        return ToolOutput(data=data)
