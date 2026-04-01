"""Document mutation tool callables — edit and write."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Annotated, Literal, override

from pydantic import Field

from .base import PathsTool, ToolOutput, file_allowed, resolve_search_path
from .documents import DocumentFilenameArg

__all__ = [
    "DocumentContentArg",
    "EditDocumentTool",
    "EditNewStringArg",
    "EditOldStringArg",
    "MutationHook",
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

MutationHook = Callable[[str], Awaitable[None]] | None
"""Optional async callback invoked after a successful write.

Receives the local (unprefixed) filename that was modified.
"""


@dataclass(slots=True, frozen=True)
class EditDocumentTool(PathsTool[str]):
    """Edit a document by replacing an exact string with a new string."""

    hook: MutationHook = None

    @override
    async def __call__(
        self,
        filename: DocumentFilenameArg,
        old_string: EditOldStringArg,
        new_string: EditNewStringArg,
    ) -> ToolOutput[str]:
        """Replace an exact string in a document.

        Fails if the string does not exist or appears more than once,
        ensuring unambiguous edits.
        """
        resolved = resolve_search_path(self.resolved_paths, filename)
        if resolved is None:
            return ToolOutput(data=f"Error: '{filename}' is not accessible.")
        sp, local = resolved
        if not file_allowed(sp.filter_func, local):
            return ToolOutput(data=f"Error: '{filename}' is not accessible.")
        file_path = (sp.path / local).resolve()
        if not file_path.is_relative_to(sp.path.resolve()):
            return ToolOutput(data="Error: path traversal detected.")
        if not file_path.is_file():
            return ToolOutput(data=f"Error: '{filename}' does not exist.")

        content = file_path.read_text(encoding="utf-8")
        count = content.count(old_string)
        if count == 0:
            return ToolOutput(data=f"Error: old_string not found in '{filename}'.")
        if count > 1:
            return ToolOutput(
                data=f"Error: old_string appears {count} times in '{filename}'; "
                "must be unique.",
            )

        new_content = content.replace(old_string, new_string, 1)
        file_path.write_text(new_content, encoding="utf-8")
        if self.hook:
            await self.hook(local)
        return ToolOutput(data=f"Replaced 1 occurrence in '{filename}'.")


@dataclass(slots=True, frozen=True)
class WriteDocumentTool(PathsTool[str]):
    """Write content to a document using prepend, append, or replace mode."""

    glob: str | None = None
    hook: MutationHook = None

    @override
    async def __call__(
        self,
        filename: DocumentFilenameArg,
        content: DocumentContentArg,
        mode: WriteModeArg = "replace",
    ) -> ToolOutput[str]:
        """Write content to a document."""
        resolved = resolve_search_path(self.resolved_paths, filename)
        if resolved is None:
            return ToolOutput(data=f"Error: '{filename}' is not accessible.")
        sp, local = resolved
        if not file_allowed(sp.filter_func, local):
            return ToolOutput(data=f"Error: '{filename}' is not accessible.")
        file_path = (sp.path / local).resolve()
        if not file_path.is_relative_to(sp.path.resolve()):
            return ToolOutput(data="Error: path traversal detected.")
        if self.glob and not PurePosixPath(local).match(self.glob):
            return ToolOutput(
                data=f"Error: '{filename}' does not match pattern '{self.glob}'.",
            )

        if mode == "replace":
            file_path.parent.mkdir(parents=True, exist_ok=True)
            file_path.write_text(content, encoding="utf-8")
            message = f"Wrote {len(content)} characters to '{filename}'."
        elif not file_path.is_file():
            return ToolOutput(
                data=f"Error: '{filename}' does not exist (use mode='replace' to create).",
            )
        elif mode == "append":
            existing = file_path.read_text(encoding="utf-8")
            file_path.write_text(existing + content, encoding="utf-8")
            message = f"Appended {len(content)} characters to '{filename}'."
        else:
            existing = file_path.read_text(encoding="utf-8")
            file_path.write_text(content + existing, encoding="utf-8")
            message = f"Prepended {len(content)} characters to '{filename}'."

        if self.hook:
            await self.hook(local)
        return ToolOutput(data=message)
