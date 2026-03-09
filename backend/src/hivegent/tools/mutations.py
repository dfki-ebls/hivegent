"""Document mutation tool callables — edit and write."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Annotated, Literal, override

from pydantic import Field

from .base import FileFilter, Tool, file_allowed
from .documents import DocumentFilenameArg

__all__ = [
    "DocumentContentArg",
    "EditDocumentTool",
    "EditNewStringArg",
    "EditOldStringArg",
    "WriteDocumentTool",
    "WriteModeArg",
]

EditOldStringArg = Annotated[
    str,
    Field(description="Exact text to replace, which must occur exactly once."),
]
EditNewStringArg = Annotated[
    str,
    Field(description="Replacement text."),
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


@dataclass(slots=True, frozen=True)
class EditDocumentTool(Tool):
    """Edit a document by replacing an exact string with a new string."""

    path: Path
    file_filter: FileFilter = None
    on_write: Callable[[DocumentFilenameArg], Awaitable[None]] | None = None

    @override
    async def __call__(
        self,
        filename: DocumentFilenameArg,
        old_string: EditOldStringArg,
        new_string: EditNewStringArg,
    ) -> str:
        """Replace an exact string in a document.

        Fails if the string does not exist or appears more than once,
        ensuring unambiguous edits.
        """
        if not file_allowed(self.file_filter, filename):
            return f"Error: '{filename}' is not accessible."
        file_path = (self.path / filename).resolve()
        if not file_path.is_relative_to(self.path.resolve()):
            return "Error: path traversal detected."
        if not file_path.is_file():
            return f"Error: '{filename}' does not exist."

        content = file_path.read_text(encoding="utf-8")
        count = content.count(old_string)
        if count == 0:
            return f"Error: old_string not found in '{filename}'."
        if count > 1:
            return (
                f"Error: old_string appears {count} times in '{filename}'; "
                "must be unique."
            )

        new_content = content.replace(old_string, new_string, 1)
        file_path.write_text(new_content, encoding="utf-8")
        if self.on_write:
            await self.on_write(filename)
        return f"Replaced 1 occurrence in '{filename}'."


@dataclass(slots=True, frozen=True)
class WriteDocumentTool(Tool):
    """Write content to a document using prepend, append, or replace mode."""

    path: Path
    glob: str | None = None
    file_filter: FileFilter = None
    on_write: Callable[[DocumentFilenameArg], Awaitable[None]] | None = None

    @override
    async def __call__(
        self,
        filename: DocumentFilenameArg,
        content: DocumentContentArg,
        mode: WriteModeArg = "replace",
    ) -> str:
        """Write content to a document."""
        if not file_allowed(self.file_filter, filename):
            return f"Error: '{filename}' is not accessible."
        file_path = (self.path / filename).resolve()
        if not file_path.is_relative_to(self.path.resolve()):
            return "Error: path traversal detected."
        if self.glob and not PurePosixPath(filename).match(self.glob):
            return f"Error: '{filename}' does not match pattern '{self.glob}'."

        if mode == "replace":
            file_path.parent.mkdir(parents=True, exist_ok=True)
            file_path.write_text(content, encoding="utf-8")
            message = f"Wrote {len(content)} characters to '{filename}'."
        elif not file_path.is_file():
            return f"Error: '{filename}' does not exist (use mode='replace' to create)."
        elif mode == "append":
            existing = file_path.read_text(encoding="utf-8")
            file_path.write_text(existing + content, encoding="utf-8")
            message = f"Appended {len(content)} characters to '{filename}'."
        else:
            existing = file_path.read_text(encoding="utf-8")
            file_path.write_text(content + existing, encoding="utf-8")
            message = f"Prepended {len(content)} characters to '{filename}'."

        if self.on_write:
            await self.on_write(filename)
        return message
