"""Document mutation tool callables — edit and write."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, override

from .typing import Tool

__all__ = ["EditDocumentTool", "WriteDocumentTool"]


@dataclass(slots=True, frozen=True)
class EditDocumentTool(Tool):
    """Edit a document by replacing an exact string with a new string."""

    path: Path
    on_write: Callable[[str], Awaitable[Any]] | None = None

    @override
    async def __call__(
        self,
        filename: str,
        old_string: str,
        new_string: str,
    ) -> str:
        """Replace an exact string in a document.

        Fails if the string does not exist or appears more than once,
        ensuring unambiguous edits.

        Args:
            filename: The relative document path.
            old_string: The exact text to replace. Must appear exactly once.
            new_string: The replacement text.
        """
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
    extension: str = ".md"
    on_write: Callable[[str], Awaitable[Any]] | None = None

    @override
    async def __call__(
        self,
        filename: str,
        content: str,
        mode: Literal["prepend", "append", "replace"] = "replace",
    ) -> str:
        """Write content to a document.

        Args:
            filename: The relative document path.
            content: The text content to write.
            mode: ``"replace"`` overwrites (creates if absent),
                ``"append"`` adds to the end,
                ``"prepend"`` adds to the start.
        """
        file_path = (self.path / filename).resolve()
        if not file_path.is_relative_to(self.path.resolve()):
            return "Error: path traversal detected."
        if self.extension and not filename.endswith(self.extension):
            return f"Error: only '{self.extension}' files are supported."

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
