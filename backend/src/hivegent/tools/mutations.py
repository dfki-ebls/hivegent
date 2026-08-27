"""Document mutation tool callables — edit and write."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Annotated, Literal, override

from fastapi import HTTPException
from pydantic import Field

from .base import (
    AsyncPathTool,
    SearchPath,
    ToolOutput,
    ToolRetry,
    resolve_accessible_file,
    workspace_root_hint,
)

__all__ = [
    "DocumentContentArg",
    "DocumentTargetPathArg",
    "EditDocumentTool",
    "EditMutation",
    "EditNewStringArg",
    "EditOldStringArg",
    "EditReplaceAllArg",
    "ExpectedHashArg",
    "MutationHint",
    "WriteDocumentTool",
    "WriteModeArg",
    "WriteMutation",
    "resolve_mutation_target",
]

DocumentTargetPathArg = Annotated[
    str,
    Field(
        description=(
            "Full workspace path of the document to mutate. A document you "
            "create needs a full path too; missing subdirectories are created."
        ),
    ),
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
ExpectedHashArg = Annotated[
    str | None,
    Field(
        description=(
            "Content hash from a prior read of this document. When given, "
            "the mutation is rejected if the document changed since, so you "
            "should re-read it and retry with the new hash. Omit to write "
            "unconditionally."
        ),
    ),
]
WriteModeArg = Annotated[
    Literal["prepend", "append", "replace", "create"],
    Field(
        description=(
            "Write mode: `replace` overwrites or creates the file, `append` "
            "adds to the end, `prepend` adds to the start, and `create` "
            "refuses to overwrite an existing file."
        ),
    ),
]

MutationHint = Callable[[str, str], str]
"""Guidance to append to a mutation's receipt, from its canonical and local paths.

Injected by the surface that has something to say rather than baked in, the way
``filter_func`` and ``mutator`` are: the agent points a stored program at
``run_python``, which the MCP surface has no tool for.  Empty for the document
it has nothing to say about, which is most of them.
"""

EditMutation = Callable[[str, str, str, bool, str | None], Awaitable[str]]
"""Canonical edit operation for a resolved document path.

The path is rendered under the search path that claimed it, so a caller
spanning several roots can route the mutation to the right one.
"""

WriteMutation = Callable[[str, str, WriteModeArg, str | None], Awaitable[str]]
"""Canonical write operation for a resolved document path, rendered as for
:data:`EditMutation`."""


def _mutation_detail(exc: HTTPException | ValueError) -> str:
    """Extract the human-readable detail from a failed-mutation exception."""
    return exc.detail if isinstance(exc, HTTPException) else str(exc)


def _hinted(hint: MutationHint | None, report: str, target: str, local: str) -> str:
    """Append the surface's pointer for this document, when it has one.

    Applied here rather than inside the mutator because this is where both
    spellings are already in hand: *local* is what a path predicate answers to
    (`.scratch/` is a location, and a canonical path is not what
    :func:`~hivegent.entries.is_scratch_path` takes), while *target* is what
    the model has to type back.
    """
    extra = hint(target, local) if hint is not None else ""

    return f"{report} {extra}" if extra else report


def resolve_mutation_target(
    paths: tuple[SearchPath, ...], file_path: str
) -> tuple[str, str, Path]:
    """Resolve *file_path* for a mutation, or raise a correctable refusal.

    Returns the path rendered under the root that claimed it (what the mutator
    routes on), the local path (what a glob is matched against), and the file
    on disk.  Unlike a read, the document need not exist: a mutation may create
    it.  It may not be a directory, though, which is the one way an unwritable
    path still resolves, refused here rather than at each surface, so a write,
    an edit, and a redirect all say the same thing.  The one place a write
    target is resolved, so every surface that stages a mutation refuses an
    unreachable path in the same words.
    """
    resolved = resolve_accessible_file(paths, file_path)
    if resolved is None:
        hint = workspace_root_hint(paths, file_path)
        raise ToolRetry(f"'{file_path}' is not accessible.{hint}")
    sp, local, absolute = resolved
    canonical = sp.prefixed(local)
    if absolute.is_dir():
        raise ToolRetry(f"'{canonical}' is a directory.")

    return canonical, local, absolute


@dataclass(slots=True, frozen=True)
class EditDocumentTool(AsyncPathTool[str]):
    """Edit a document by replacing an exact string with a new string.

    Resolves and access-checks the path, then delegates the mutation to
    :attr:`mutator` — the canonical workspace gateway that owns the
    string-replacement semantics and re-indexing.
    """

    mutator: EditMutation = field(kw_only=True)
    hint: MutationHint | None = None

    @override
    async def __call__(
        self,
        file_path: DocumentTargetPathArg,
        old_string: EditOldStringArg,
        new_string: EditNewStringArg,
        replace_all: EditReplaceAllArg = False,
        expected_hash: ExpectedHashArg = None,
    ) -> ToolOutput[str]:
        """Replace an exact string in a document.

        By default the match must be unique — fails if ``old_string`` does
        not exist or appears more than once.  Pass ``replace_all=True`` to
        substitute every occurrence instead.  Pass ``expected_hash`` from a
        prior read to reject the edit if the document changed since.

        Anything ``read_document`` can read, this can edit: a markdown
        document, and equally a plain-text original such as a config,
        data, or source file, whose searchable markdown is regenerated
        from the new content automatically.  A binary (PDF, Office
        document, image, video) cannot be edited — replace it by
        uploading a new version instead.
        """
        target, local, _absolute = resolve_mutation_target(
            self.resolved_paths, file_path
        )
        try:
            data = await self.mutator(
                target, old_string, new_string, replace_all, expected_hash
            )
        except (HTTPException, ValueError) as exc:
            raise ToolRetry(_mutation_detail(exc)) from exc
        return ToolOutput(data=_hinted(self.hint, data, target, local))


@dataclass(slots=True, frozen=True)
class WriteDocumentTool(AsyncPathTool[str]):
    """Write content to a document using the requested write mode.

    Resolves and access-checks the path (optionally enforcing :attr:`glob`),
    then delegates the mutation to :attr:`mutator` — the canonical workspace
    gateway that owns the write-mode semantics and re-indexing.
    """

    glob: str | None = None
    hint: MutationHint | None = None
    mutator: WriteMutation = field(kw_only=True)

    @override
    async def __call__(
        self,
        file_path: DocumentTargetPathArg,
        content: DocumentContentArg,
        mode: WriteModeArg = "replace",
        expected_hash: ExpectedHashArg = None,
    ) -> ToolOutput[str]:
        """Write content to a document.

        Pass ``expected_hash`` from a prior read to reject the write if the
        document changed since.

        Anything ``read_document`` can read, this can rewrite: a markdown
        document, and equally a plain-text original such as a config,
        data, or source file, whose searchable markdown is regenerated
        from the new content automatically.  A binary (PDF, Office
        document, image, video) cannot be written — replace it by
        uploading a new version instead.  New documents can be created as
        markdown or as a plain-text format; any other format has to be
        uploaded.
        """
        target, local, _absolute = resolve_mutation_target(
            self.resolved_paths, file_path
        )
        if self.glob and not PurePosixPath(local).match(self.glob):
            raise ToolRetry(f"'{file_path}' does not match pattern '{self.glob}'.")
        try:
            data = await self.mutator(target, content, mode, expected_hash)
        except (HTTPException, ValueError) as exc:
            raise ToolRetry(_mutation_detail(exc)) from exc
        return ToolOutput(data=_hinted(self.hint, data, target, local))
