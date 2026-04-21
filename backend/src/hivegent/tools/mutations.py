"""Document mutation tool callables — edit and write."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Annotated, Literal, override

from pydantic import Field

from .base import AsyncPathTool, ToolOutput, resolve_accessible_file
from .documents import DocumentFilePathArg

__all__ = [
    "DocumentContentArg",
    "EditDocumentTool",
    "EditNewStringArg",
    "EditOldStringArg",
    "EditReplaceAllArg",
    "MutationHook",
    "WriteDocumentTool",
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

MutationHook = Callable[[str], Awaitable[None]] | None
"""Optional async callback invoked after a successful write.

Receives the local (unprefixed) filename that was modified.
"""


@dataclass(slots=True, frozen=True)
class EditDocumentTool(AsyncPathTool[str]):
    """Edit a document by replacing an exact string with a new string."""

    hook: MutationHook = None

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
        _sp, local, absolute = resolved
        if not absolute.is_file():
            return ToolOutput(data=f"Error: '{file_path}' does not exist.")

        content = absolute.read_text(encoding="utf-8")
        count = content.count(old_string)
        if count == 0:
            return ToolOutput(data=f"Error: old_string not found in '{file_path}'.")
        if count > 1 and not replace_all:
            return ToolOutput(
                data=(
                    f"Error: old_string appears {count} times in '{file_path}'; "
                    "must be unique or call with replace_all=True."
                ),
            )

        new_content = (
            content.replace(old_string, new_string)
            if replace_all
            else content.replace(old_string, new_string, 1)
        )
        absolute.write_text(new_content, encoding="utf-8")
        if self.hook:
            await self.hook(local)
        replaced = count if replace_all else 1
        noun = "occurrence" if replaced == 1 else "occurrences"
        return ToolOutput(data=f"Replaced {replaced} {noun} in '{file_path}'.")


@dataclass(slots=True, frozen=True)
class WriteDocumentTool(AsyncPathTool[str]):
    """Write content to a document using prepend, append, or replace mode."""

    glob: str | None = None
    hook: MutationHook = None

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
        _sp, local, absolute = resolved
        if self.glob and not PurePosixPath(local).match(self.glob):
            return ToolOutput(
                data=f"Error: '{file_path}' does not match pattern '{self.glob}'.",
            )

        if mode == "replace":
            absolute.parent.mkdir(parents=True, exist_ok=True)
            absolute.write_text(content, encoding="utf-8")
            message = f"Wrote {len(content)} characters to '{file_path}'."
        elif not absolute.is_file():
            return ToolOutput(
                data=(
                    f"Error: '{file_path}' does not exist "
                    "(use mode='replace' to create)."
                ),
            )
        elif mode == "append":
            existing = absolute.read_text(encoding="utf-8")
            absolute.write_text(existing + content, encoding="utf-8")
            message = f"Appended {len(content)} characters to '{file_path}'."
        else:
            existing = absolute.read_text(encoding="utf-8")
            absolute.write_text(content + existing, encoding="utf-8")
            message = f"Prepended {len(content)} characters to '{file_path}'."

        if self.hook:
            await self.hook(local)
        return ToolOutput(data=message)
