"""Document mutation tool callables, which edit, write, move, and delete.

Moves and deletes take a list and edits take a list of replacements, so a
batch is one call, one approval, and one all-or-nothing commit, and a single item
is a list of one.  No argument takes a glob: the caller enumerates the paths,
which is what the approval shows and what lands.
"""

import csv
from collections.abc import Awaitable, Callable, Generator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from io import StringIO
from pathlib import Path, PurePosixPath
from typing import Annotated, override

from fastapi import HTTPException
from pydantic import Field

from ..changes import (
    Changeset,
    Delete,
    Edit,
    Move,
    Operation,
    TextEdit,
    Write,
    WriteMode,
)
from ..converters import BINARY_WRITE_REASON, DELIMITERS, writes_as_text
from ..humanize import pluralize
from .base import (
    AsyncPathTool,
    Direct,
    SearchPath,
    ToolOutput,
    ToolRetry,
    batch_field,
    entry_stat,
    near_miss_hint,
    policy_of,
    resolve_accessible_file,
    resolve_file_or_retry,
    workspace_root_hint,
)

__all__ = [
    "MAX_BATCH",
    "Commit",
    "DeleteDocumentsTool",
    "DeletePathsArg",
    "DocumentContentArg",
    "DocumentEditsArg",
    "DocumentMove",
    "DocumentMovesArg",
    "DocumentTargetPathArg",
    "EditDocumentTool",
    "ExpectedHashArg",
    "MoveDocumentsTool",
    "MutationHint",
    "WriteDocumentTool",
    "WriteModeArg",
    "check_delimited_rows",
    "delete_changeset",
    "edit_changeset",
    "move_changeset",
    "mutation_errors",
    "partition",
    "resolve_text_target",
    "write_changeset",
]

MAX_BATCH = 100
"""Most items one mutation call may carry, which keeps one approval readable."""

DocumentTargetPathArg = Annotated[
    str,
    Field(
        description=(
            "Full workspace path of the document to mutate. A document you "
            "create needs a full path too; missing subdirectories are created."
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


DocumentEditsArg = Annotated[
    list[TextEdit],
    batch_field(
        (
            "Replacements applied in order, each to the text the previous one "
            "left, and saved as one write. Put every change to this document "
            "in one call rather than one call per change."
        ),
        max_items=MAX_BATCH,
    ),
]


@dataclass(slots=True, frozen=True)
class DocumentMove:
    """One document or directory and where it goes."""

    source: Annotated[
        str,
        Field(
            description=(
                "Full workspace path of the document or directory to move, as "
                "it is now, before any move of this call."
            ),
        ),
    ]
    destination: Annotated[
        str,
        Field(
            description=(
                "Full workspace path where it ends up once every move of this "
                "call applied, which renames it when only the last segment "
                "differs and re-homes it in another workspace when the prefix "
                "does. A document's extension follows it and cannot be changed "
                "by moving. A path naming a directory that stays where it is "
                "moves the source into it. It must be free, or be vacated by "
                "another move of this call."
            ),
        ),
    ]


DocumentMovesArg = Annotated[
    list[DocumentMove],
    batch_field(
        (
            "Every move to make, applied at once or not at all: sources are "
            "paths as they are now, destinations where they end up, so chains, "
            "swaps, and moves into or out of a directory another move takes "
            "fit in one call. Each source may appear in one move only."
        ),
        max_items=MAX_BATCH,
    ),
]
DeletePathsArg = Annotated[
    list[str],
    batch_field(
        (
            "Full workspace paths of the documents to delete, each of which "
            "must exist, listed one by one. They are deleted together or not at all."
        ),
        max_items=MAX_BATCH,
    ),
]
WriteModeArg = Annotated[
    WriteMode,
    Field(
        description=(
            "Write mode: `replace` overwrites or creates the file, `append` "
            "adds to the end, `prepend` adds to the start, and `create` "
            "refuses to overwrite an existing file."
        ),
    ),
]

MutationHint = Callable[[str], str]
"""Guidance to append to a mutation's receipt, from the canonical path it changed.

Injected by the surface that has something to say rather than baked in, the way
``filter_func`` and ``commit`` are: the agent points a stored program at
``run_python``, which the MCP surface has no tool for.  Empty for the document
it has nothing to say about, which is most of them.
"""

Commit = Callable[[Changeset[str]], Awaitable[str]]
"""Apply a changeset spelled in canonical paths, returning what it did.

Injected by the surface, which routes each path back to the root that claimed
it, so a batch may span the personal workspace and a group's, and lands each
root's half the way that root's :class:`~hivegent.tools.base.CommitPolicy` says.
"""


@contextmanager
def mutation_errors(into: Callable[[str], Exception], suffix: str = "") -> Generator[None]:
    """Re-raise a refused mutation as *into* its detail, *suffix* appended as a sentence.

    The gateway refuses with an ``HTTPException`` and a route it cannot find with
    a ``ValueError``, and this is the one place either becomes the surface's own
    correctable error, the way :func:`~hivegent.tools.base.translate_tool_retry`
    is for a :class:`ToolRetry`.
    """
    try:
        yield
    except (HTTPException, ValueError) as exc:
        detail = str(exc.detail if isinstance(exc, HTTPException) else exc)
        raise into(f"{detail.rstrip('.')}. {suffix}" if suffix else detail) from exc


def partition(
    paths: tuple[SearchPath, ...], changeset: Changeset[str]
) -> tuple[Changeset[str], Changeset[str]]:
    """Split *changeset* into the operations its roots gate and the ones they take directly.

    A move between a gated root and a direct one is refused, since no single
    commit covers both ends: its text is written at the destination and the
    source deleted instead.
    """
    gated: list[Operation[str]] = []
    direct: list[Operation[str]] = []

    for op in changeset.operations:
        policies = {policy_of(paths, location) for location in Changeset((op,)).locations}

        if isinstance(op, Move) and len(policies) > 1:
            raise ToolRetry(
                f"'{op.source}' cannot move to '{op.destination}', since only one of "
                "them is written directly. Write its text to the destination and "
                "delete the source instead."
            )

        (direct if isinstance(policies.pop(), Direct) else gated).append(op)

    return Changeset(tuple(gated)), Changeset(tuple(direct))


def _hinted(hint: MutationHint | None, report: str, target: str) -> str:
    """Append the surface's pointer for the document *target*, when it has one."""
    extra = hint(target) if hint is not None else ""

    return f"{report} {extra}" if extra else report


def _resolve_target(paths: tuple[SearchPath, ...], file_path: str) -> tuple[str, Path]:
    """Resolve *file_path* for a mutation, which need not exist, or refuse it.

    Returns the path rendered under the root that claimed it (what the commit
    routes on) and the file on disk.  It may name a directory, which for a move
    destination means moving into it, the ``mv`` semantics the gateway applies.
    What a directory cannot be is the target of a text write, which is where
    :func:`resolve_text_target` refuses it.
    """
    resolved = resolve_accessible_file(paths, file_path)
    if resolved is None:
        hint = workspace_root_hint(paths, file_path)
        raise ToolRetry(f"'{file_path}' is not accessible.{hint}")

    sp, local, absolute = resolved

    return sp.prefixed(local), absolute


_NOT_A_VALUE = frozenset({"None", "nan", "NaN", "NaT", "null", "undefined"})
"""How a language spells a missing value when a cell was interpolated, not rendered.

Every one of these reaches a delimited file the same way — an f-string handed
the value rather than the text for it — and reads downstream as the four-letter
word it is rather than as the gap it means.  Deliberately not `NA` or `N/A`,
which a lab writes on purpose.
"""


def check_delimited_rows(canonical_path: str, content: str) -> None:
    """Refuse a delimited file whose rows are not all the header's width.

    A table built a row at a time is one f-string away from a row with a field
    too many or too few, and nothing downstream reports it: every value on that
    row lands under the wrong heading, where it reads as a measurement of
    something it is not.  The header decides the width, since it is the one row
    whose shape is a statement about the file, and the suffix decides the
    separator through :data:`~hivegent.converters.DELIMITERS`, the same table
    the query tool's loader reads — so the surface that writes a `.csv` and the
    surface that reads one back can never disagree about what its fields are.

    Read as a stream rather than a list, so the bad row this exists to catch
    stops the scan where it is, and so a newline inside a quoted cell stays
    inside it, which splitting the text into lines first would not.

    Only whole writes are checked, which is where a generated table arrives:
    the write tool and a program's committed output.  An edit replaces a
    string inside a file it did not build and is left alone.  It stays at the
    tool layer rather than in the gateway, unlike ``writes_as_text``: a binary
    write is impossible, while a ragged row is only probably a mistake, and a
    person typing in the document editor is entitled to save one.
    """
    delimiter = DELIMITERS.get(PurePosixPath(canonical_path).suffix.lower())

    if delimiter is None:
        return

    rows = csv.reader(StringIO(content), delimiter=delimiter)
    width = len(next(rows, ()))

    # One column has no width to disagree with, which is also what a file that
    # is not really delimited looks like here — and not ours to judge further.
    if width < 2:
        return

    for number, row in enumerate(rows, start=2):
        if len(row) != width and any(cell.strip() for cell in row):
            raise ToolRetry(
                f"'{canonical_path}' line {number} has {len(row)} "
                f"{pluralize(len(row), 'field')} where the header has {width}, "
                "so every value on it lands under the wrong column. Build each "
                "row as a list of cells, one per header, and join them once, "
                "rather than writing the separators by hand; a row carrying "
                "only a summary still needs the empty cells around it."
            )

        if placeholder := next((c for c in row if c.strip() in _NOT_A_VALUE), None):
            raise ToolRetry(
                f"'{canonical_path}' line {number} carries the cell "
                f"'{placeholder}', which is a language's name for a missing "
                "value rather than one a reader of this file can use. A missing "
                "cell is the empty string: render each cell yourself instead of "
                "interpolating whatever the value happens to be."
            )


def resolve_text_target(paths: tuple[SearchPath, ...], file_path: str) -> str:
    """Resolve *file_path* for a mutation that writes text at it, to its canonical path.

    This adds the two questions every text write shares and a move or a delete
    does not, so the surfaces that write text (the write tool and the edit
    tool) refuse a directory and a binary target in
    the same words the gateway would, before an approval is asked for or a
    program is run.

    The format question is asked of a *new* document only, which is the
    gateway's own condition (``current is None and not writes_as_text``): a
    file that already exists is answered from its bytes by the decoder, the
    same question the read tools ask, and a name table has no business
    overruling it.
    """
    canonical, absolute = _resolve_target(paths, file_path)

    if absolute.is_dir():
        raise ToolRetry(f"'{canonical}' is a directory.")

    if not absolute.is_file() and not writes_as_text(canonical):
        raise ToolRetry(f"'{canonical}' {BINARY_WRITE_REASON}.")

    return canonical


def write_changeset(
    paths: tuple[SearchPath, ...],
    file_path: str,
    content: str,
    mode: WriteModeArg = "replace",
    expected_hash: str | None = None,
) -> Changeset[str]:
    """The changeset :class:`WriteDocumentTool` commits, refused as it would be."""
    target = resolve_text_target(paths, file_path)
    check_delimited_rows(target, content)

    return Changeset((Write(target, content, mode, expected_hash),))


def edit_changeset(
    paths: tuple[SearchPath, ...],
    file_path: str,
    edits: Sequence[TextEdit],
    expected_hash: str | None = None,
) -> Changeset[str]:
    """The changeset :class:`EditDocumentTool` commits, refused as it would be."""
    target = resolve_text_target(paths, file_path)

    return Changeset((Edit(target, tuple(edits), expected_hash),))


def move_changeset(
    paths: tuple[SearchPath, ...], moves: Sequence[DocumentMove]
) -> Changeset[str]:
    """The changeset :class:`MoveDocumentsTool` commits, or a refusal of the batch.

    A source must exist, as a document or a directory, but a destination need not,
    since naming a missing one is how a document is renamed.  Whether the moves
    fit together is the gateway's question, asked of the whole batch at once.
    """
    operations: list[Move[str]] = []

    for move in moves:
        source, absolute = _resolve_target(paths, move.source)

        if entry_stat(absolute) is None:
            raise ToolRetry(f"'{move.source}' not found.{near_miss_hint(absolute)}")

        destination, _absolute = _resolve_target(paths, move.destination)
        operations.append(Move(source, destination))

    return Changeset(tuple(operations))


def delete_changeset(
    paths: tuple[SearchPath, ...], file_paths: Sequence[str]
) -> Changeset[str]:
    """The changeset :class:`DeleteDocumentsTool` commits, or a refusal of the batch.

    A directory is refused like any reader refuses one: deleting its documents
    means naming them, so what the user approves is exactly what goes.
    """
    operations: list[Delete[str]] = []

    for file_path in file_paths:
        sp, local, _absolute = resolve_file_or_retry(paths, file_path)
        operations.append(Delete(sp.prefixed(local)))

    return Changeset(tuple(operations))


@dataclass(slots=True, frozen=True)
class EditDocumentTool(AsyncPathTool[str]):
    """Edit a document by replacing exact strings with new strings.

    Resolves and access-checks the path, then hands the edit to :attr:`commit`,
    which owns the string-replacement semantics and re-indexing.
    """

    commit: Commit = field(kw_only=True)
    hint: MutationHint | None = None

    @override
    async def __call__(
        self,
        file_path: DocumentTargetPathArg,
        edits: DocumentEditsArg,
        expected_hash: ExpectedHashArg = None,
    ) -> ToolOutput[str]:
        """Replace exact strings in a document, one or many in a single write.

        The edits apply in order, each to the text the previous one left, and
        either all land or none do.  By default an ``old_string`` must match
        exactly once.  Set ``replace_all`` on an edit to substitute every
        occurrence instead.  Pass ``expected_hash`` from a prior read to reject
        the edit if the document changed since.

        Anything ``read_document`` can read, this can edit: a markdown
        document, and equally an original such as a config, data
        (``.csv``), markup, or source file, whose searchable markdown is
        regenerated from the new content automatically.  A binary (PDF,
        Office document, spreadsheet, image, video) cannot be edited —
        replace it by uploading a new version instead.
        """
        changeset = edit_changeset(self.resolved_paths, file_path, edits, expected_hash)

        with mutation_errors(ToolRetry):
            data = await self.commit(changeset)

        return ToolOutput(data=_hinted(self.hint, data, changeset.locations[0]))


@dataclass(slots=True, frozen=True)
class WriteDocumentTool(AsyncPathTool[str]):
    """Write content to a document using the requested write mode.

    Resolves and access-checks the path, then hands the write to
    :attr:`commit`, which owns the write-mode semantics and re-indexing.
    """

    hint: MutationHint | None = None
    commit: Commit = field(kw_only=True)

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
        changeset = write_changeset(
            self.resolved_paths, file_path, content, mode, expected_hash
        )

        with mutation_errors(ToolRetry):
            data = await self.commit(changeset)

        return ToolOutput(data=_hinted(self.hint, data, changeset.locations[0]))


@dataclass(slots=True, frozen=True)
class MoveDocumentsTool(AsyncPathTool[str]):
    """Move or rename documents and directories, all together or not at all.

    Both ends of every move are resolved against the writable span, so a
    cross-workspace move is allowed exactly when the run may write the source
    and the destination, and :attr:`commit` routes each end back to its own
    root.
    """

    commit: Commit = field(kw_only=True)

    @override
    async def __call__(self, moves: DocumentMovesArg) -> ToolOutput[str]:
        """Move documents or directories, renaming them when the name differs.

        This is how documents are renamed or re-filed: an entry keeps its
        content, its extension, its original, and its extracted assets, and
        nothing is re-converted or re-indexed, so it costs far less than
        writing the content out at a new path and deleting the old one.  A
        directory moves with everything in it, and a destination in another
        workspace re-homes the source there.  Put every move of one task in a
        single call: the moves apply at once, so renaming a to b and b to c,
        or swapping two names, is one call, and the batch is checked as a
        whole before anything moves and lands completely or not at all.
        """
        changeset = move_changeset(self.resolved_paths, moves)

        with mutation_errors(ToolRetry):
            return ToolOutput(data=await self.commit(changeset))


@dataclass(slots=True, frozen=True)
class DeleteDocumentsTool(AsyncPathTool[str]):
    """Delete documents with everything derived from them, all or none."""

    commit: Commit = field(kw_only=True)

    @override
    async def __call__(self, paths: DeletePathsArg) -> ToolOutput[str]:
        """Delete documents and everything belonging to them.

        The searchable markdown, the original it was projected from, its
        extracted assets, and its index entries all go with each document, so
        it stops being reachable by search, read, or path.  List every document
        to delete in one call, and they are removed together or not at all.  This
        cannot be undone: ask the user before deleting anything they did not
        name.
        """
        changeset = delete_changeset(self.resolved_paths, paths)

        with mutation_errors(ToolRetry):
            return ToolOutput(data=await self.commit(changeset))
