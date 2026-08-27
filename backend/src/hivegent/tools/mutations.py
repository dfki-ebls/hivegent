"""Document mutation tool callables — edit, write, move, and delete."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Annotated, Literal, override

from fastapi import HTTPException
from pydantic import Field

from ..converters import BINARY_WRITE_REASON, writes_as_text
from ..entries import SCRATCH_DIR_NAME, is_scratch_path
from .base import (
    AsyncPathTool,
    SearchPath,
    ToolOutput,
    ToolRetry,
    resolve_accessible_file,
    resolve_file_or_retry,
    workspace_root_hint,
)

__all__ = [
    "DeleteDocumentTool",
    "DeleteMutation",
    "DocumentContentArg",
    "DocumentDestinationArg",
    "DocumentTargetPathArg",
    "EditDocumentTool",
    "EditMutation",
    "EditNewStringArg",
    "EditOldStringArg",
    "EditReplaceAllArg",
    "ExistingDocumentPathArg",
    "ExpectedHashArg",
    "MoveDocumentTool",
    "MoveMutation",
    "MutationHint",
    "WriteDocumentTool",
    "WriteModeArg",
    "WriteMutation",
    "resolve_mutation_target",
    "resolve_text_target",
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
ExistingDocumentPathArg = Annotated[
    str,
    Field(
        description=(
            "Full workspace path of the document to act on, which must already exist."
        ),
    ),
]
DocumentDestinationArg = Annotated[
    str,
    Field(
        description=(
            "Full workspace path to move the document to, which renames it "
            "when only the last segment differs and re-homes it in another "
            "workspace when the prefix does. The extension follows the "
            "document and cannot be changed by moving; a path naming an "
            "existing directory moves the document into it."
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

MoveMutation = Callable[[str, str], Awaitable[None]]
"""Canonical move operation, taking both ends as canonical paths.

Two paths rather than one because the ends may name different workspaces, which
is what makes a move the one mutation that can cross from the personal
workspace into a group's.
"""

DeleteMutation = Callable[[str], Awaitable[None]]
"""Canonical delete operation for a resolved document path."""


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
    it, and it may name a directory, which for a move destination means moving
    into it (the ``mv`` semantics
    :func:`~hivegent.workspace.paths._resolve_move_destination` applies).  What
    a directory cannot be is the target of a *text write*, which is where
    :func:`resolve_text_target` refuses it.
    """
    resolved = resolve_accessible_file(paths, file_path)
    if resolved is None:
        hint = workspace_root_hint(paths, file_path)
        raise ToolRetry(f"'{file_path}' is not accessible.{hint}")
    sp, local, absolute = resolved

    return sp.prefixed(local), local, absolute


def _check_not_scratch(canonical: str, local: str) -> None:
    """Refuse a `.scratch/` move source, which has no entry to relocate.

    A move relocates an entry — its description, its original, its assets, and
    its rows — and a scratch file is bytes with none of those, so the gateway
    would answer with a bare "not found" that says nothing about why.  Only the
    source needs this: a scratch *destination* is a reserved path the gateway
    already refuses by name, in one voice with `.assets`
    (:func:`~hivegent.workspace.paths._check_not_reserved_path`), and writing
    and deleting reach scratch freely.
    """
    if is_scratch_path(local):
        raise ToolRetry(
            f"'{canonical}' is under `{SCRATCH_DIR_NAME}/`, which holds working "
            "state rather than entries, so there is no document to move. Read "
            "it and write the text to a document path instead."
        )


def resolve_text_target(
    paths: tuple[SearchPath, ...], file_path: str
) -> tuple[str, str, Path]:
    """Resolve *file_path* for a mutation that writes text at it.

    :func:`resolve_mutation_target` answers where the path is; this adds the
    two questions every text write shares and a move or a delete does not, so
    the surfaces that write text — the write tool, the edit tool, and a
    redirected ``output_path`` — refuse a directory and a binary target in the
    same words the gateway would, before an approval is asked for or a program
    is run.

    The format question is asked of a *new* document only, which is the
    gateway's own condition (``current is None and not writes_as_text``): a
    file that already exists is answered from its bytes by the decoder, the
    same question the read tools ask, and a name table has no business
    overruling it.  ``is_scratch_path`` comes before both, here as in the
    gateway, since a scratch file is bytes the run owns with no entry, no
    projection, and no converter to hand them to.
    """
    canonical, local, absolute = resolve_mutation_target(paths, file_path)
    if absolute.is_dir():
        raise ToolRetry(f"'{canonical}' is a directory.")

    if (
        not is_scratch_path(local)
        and not absolute.is_file()
        and not writes_as_text(canonical)
    ):
        raise ToolRetry(f"'{canonical}' {BINARY_WRITE_REASON}.")

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
        document, and equally an original such as a config, data
        (``.csv``), markup, or source file, whose searchable markdown is
        regenerated from the new content automatically.  A binary (PDF,
        Office document, spreadsheet, image, video) cannot be edited —
        replace it by uploading a new version instead.
        """
        target, local, _absolute = resolve_text_target(self.resolved_paths, file_path)
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

        Anything ``read_document`` can read, this can rewrite, and any
        text format can be created: a markdown document, and equally an
        original such as a config, data (``.csv``), markup, or source
        file, whose searchable markdown is regenerated from the new
        content automatically.  A binary (PDF, Office document,
        spreadsheet, image, video) cannot be written — replace it by
        uploading a new version instead.
        """
        target, local, _absolute = resolve_text_target(self.resolved_paths, file_path)
        if self.glob and not PurePosixPath(local).match(self.glob):
            raise ToolRetry(f"'{file_path}' does not match pattern '{self.glob}'.")
        try:
            data = await self.mutator(target, content, mode, expected_hash)
        except (HTTPException, ValueError) as exc:
            raise ToolRetry(_mutation_detail(exc)) from exc
        return ToolOutput(data=_hinted(self.hint, data, target, local))


@dataclass(slots=True, frozen=True)
class MoveDocumentTool(AsyncPathTool[str]):
    """Move or rename a document, its original, and its child assets.

    Both ends are resolved against the writable span, so a cross-workspace
    move is allowed exactly when the run may write the source and the
    destination, and :attr:`mutator` routes each end back to its own store.
    """

    mutator: MoveMutation = field(kw_only=True)

    @override
    async def __call__(
        self,
        file_path: ExistingDocumentPathArg,
        destination: DocumentDestinationArg,
    ) -> ToolOutput[str]:
        """Move a document to another path, renaming it when the name differs.

        This is how a document is renamed or re-filed: the entry keeps its
        content, its extension, its original, and its extracted assets, and
        nothing is re-converted or re-indexed, so it costs far less than
        writing the content out at a new path and deleting the old one.  A
        destination in another workspace re-homes the document there.
        """
        sp, local, _absolute = resolve_file_or_retry(self.resolved_paths, file_path)
        target = sp.prefixed(local)
        _check_not_scratch(target, local)
        moved_to, _local, _dst = resolve_mutation_target(
            self.resolved_paths, destination
        )
        try:
            await self.mutator(target, moved_to)
        except (HTTPException, ValueError) as exc:
            raise ToolRetry(_mutation_detail(exc)) from exc

        return ToolOutput(data=f"Moved '{target}' to '{moved_to}'.")


@dataclass(slots=True, frozen=True)
class DeleteDocumentTool(AsyncPathTool[str]):
    """Delete a document with everything derived from it."""

    mutator: DeleteMutation = field(kw_only=True)

    @override
    async def __call__(self, file_path: ExistingDocumentPathArg) -> ToolOutput[str]:
        """Delete a document and everything belonging to it.

        The searchable markdown, the original it was projected from, its
        extracted assets, and its index entries all go with it, so the
        document stops being reachable by search, read, or path.  A `.scratch/`
        file is removed too, which is how a run clears working state it no
        longer needs.  This cannot be undone: ask the user before deleting
        anything they did not name.
        """
        sp, local, _absolute = resolve_file_or_retry(self.resolved_paths, file_path)
        target = sp.prefixed(local)
        try:
            await self.mutator(target)
        except (HTTPException, ValueError) as exc:
            raise ToolRetry(_mutation_detail(exc)) from exc

        return ToolOutput(data=f"Deleted '{target}'.")
