"""The plain data a workspace change is made of, and what approving one shows.

The operations are generic over how they name a path: ``L`` is a canonical
``str`` (``~/a.md``, ``@team/b.md``) where a tool or a staged program spells
one, and a :class:`~hivegent.workspace.operations.Location` once
:func:`~hivegent.workspace.operations.route` sent it to its root.  Every
type here is a frozen dataclass that ``pydantic.TypeAdapter(Changeset[str])``
serializes and validates, which is what lets a changeset be stored and applied
later.

No store and no gateway: :mod:`hivegent.tools` builds these without reaching
the workspace, and :mod:`hivegent.workspace.changeset` plans and applies them.
"""

import difflib
from dataclasses import dataclass
from typing import Annotated, Literal

from pydantic import Field

from .chunkers import ChunkingSpec
from .entries import ContentStat

__all__ = [
    "MAX_DIFF_CHARS",
    "MAX_SUMMARY_DIFF_CHARS",
    "Basis",
    "Changeset",
    "ChangesetSummary",
    "CreateDir",
    "Delete",
    "DeleteKind",
    "Edit",
    "FileDiff",
    "Move",
    "Operation",
    "PathMove",
    "TextEdit",
    "WorkspaceChanged",
    "Write",
    "WriteMode",
    "capped_diff",
]

type WriteMode = Literal["replace", "create", "append", "prepend"]
"""How a write composes its text with the document's current content."""

type DeleteKind = Literal["entry", "dir"]
"""What a delete expects its target to be, refused when it is the other."""

type Basis = str | ContentStat
"""What a caller saw when it last read a file, to refuse a change made since.

Either the content hash the read tools report, or the file's
:class:`~hivegent.entries.ContentStat` for a caller that never decoded it (a
sandbox overlay diffing a mount).  Always about a path as it is before the
changeset, whatever else the changeset does to it.
"""


@dataclass(slots=True, frozen=True)
class TextEdit:
    """One exact-string replacement of an edit."""

    old_string: Annotated[
        str,
        Field(
            description=(
                "Exact text to replace.  Must occur exactly once unless "
                "`replace_all` is true."
            ),
            min_length=1,
        ),
    ]
    new_string: Annotated[str, Field(description="Replacement text.")]
    replace_all: Annotated[
        bool,
        Field(
            description=(
                "When true, replace every occurrence of `old_string` instead "
                "of requiring a unique match.  Use this for renames and "
                "global substitutions."
            ),
        ),
    ] = False


@dataclass(slots=True, frozen=True)
class Write[L]:
    """Set a text document's content, creating it when it is missing.

    Attributes:
        target: The document, where it is once the changeset applied.
        content: The text to write.
        mode: How *content* composes with what the document holds.
        basis: What the caller last saw of the document, if anything.
        chunking: The chunking pipeline to index it with.
    """

    target: L
    content: str
    mode: WriteMode = "replace"
    basis: Basis | None = None
    chunking: ChunkingSpec | None = None
    kind: Literal["write"] = "write"


@dataclass(slots=True, frozen=True)
class Edit[L]:
    """Apply exact-string replacements in order, as one write.

    Attributes:
        target: The document, where it is once the changeset applied, which
            must exist then.
        edits: The replacements, each seeing the text the previous one left.
        basis: What the caller last saw of the document, if anything.
    """

    target: L
    edits: tuple[TextEdit, ...]
    basis: Basis | None = None
    kind: Literal["edit"] = "edit"


@dataclass(slots=True, frozen=True)
class Move[L]:
    """Relocate an entry or a directory, renaming it when the name differs.

    Attributes:
        source: The entry (by its description or original) or the directory,
            as it is now.
        destination: Where it ends up.  An existing directory that stays
            where it is means into it.
        basis: What the caller last saw of the source file, if anything.
    """

    source: L
    destination: L
    basis: Basis | None = None
    kind: Literal["move"] = "move"


@dataclass(slots=True, frozen=True)
class Delete[L]:
    """Remove an entry with everything derived from it, or a directory.

    Attributes:
        target: What to remove, as it is now.
        basis: What the caller last saw of the file, if anything.
        expect: What *target* has to be, either kind when ``None``.
    """

    target: L
    basis: Basis | None = None
    expect: DeleteKind | None = None
    kind: Literal["delete"] = "delete"


@dataclass(slots=True, frozen=True)
class CreateDir[L]:
    """Create an empty directory, which nothing else puts there.

    Attributes:
        target: The directory, where it is once the changeset applied.
    """

    target: L
    kind: Literal["mkdir"] = "mkdir"


type Operation[L] = Annotated[
    Write[L] | Edit[L] | Move[L] | Delete[L] | CreateDir[L], Field(discriminator="kind")
]
"""One item of a :class:`Changeset`."""


@dataclass(slots=True, frozen=True)
class Changeset[L]:
    """A batch of workspace operations that commits all or nothing, at once.

    Attributes:
        operations: The items, in the order their reports come back.  They
            apply simultaneously rather than in order: every source and basis
            names the workspace before the changeset, and every destination
            and target the workspace after it.
    """

    operations: tuple[Operation[L], ...]

    @property
    def locations(self) -> tuple[L, ...]:
        """Every path the changeset names, in order.

        >>> Changeset((Move("~/a.md", "~/b.md"), Delete("~/c.md"))).locations
        ('~/a.md', '~/b.md', '~/c.md')
        """
        return tuple(
            location
            for op in self.operations
            for location in (
                (op.source, op.destination) if isinstance(op, Move) else (op.target,)
            )
        )


MAX_DIFF_CHARS = 4_000
"""Most characters of one file's diff a summary carries."""

MAX_SUMMARY_DIFF_CHARS = 40_000
"""Most characters of diff one summary carries across all of its files."""


@dataclass(slots=True, frozen=True)
class FileDiff:
    """A created or updated file and its unified diff, capped."""

    path: str
    diff: str


@dataclass(slots=True, frozen=True)
class PathMove:
    """A document file or directory, where it moves, and whether that replaces a file."""

    source: str
    destination: str
    is_dir: bool = False
    replaces: bool = False


@dataclass(slots=True, frozen=True)
class WorkspaceChanged:
    """The workspaces a change touched, and the paths it moved and deleted.

    What a commit of the gateway returns and announces, and what a mutation
    outside it announces with no moves or deletes, so a client refreshes the
    scopes and follows the paths at once.  Paths are canonical and name
    workspace files and directories only, since a folder such as ``/tmp``
    holds nothing a client tracks: an entry's description and original each,
    and a directory as a whole.  Sources and deletes name the workspace before
    the change and destinations the one after it.

    Attributes:
        scopes: The prefix of every workspace it touched, such as ``~``.
        moves: Every moved file and directory.
        deletes: Every deleted file and directory.
        type: The feed's discriminator.
    """

    scopes: tuple[str, ...]
    moves: tuple[PathMove, ...] = ()
    deletes: tuple[str, ...] = ()
    type: Literal["workspace-changed"] = "workspace-changed"


@dataclass(slots=True, frozen=True)
class ChangesetSummary:
    """What a changeset does, as a person approving it reads it.

    Attributes:
        creates: The new files with their capped diffs.
        updates: The changed files with their capped diffs.
        moves: The moved files and directories, as a commit reports them.
        deletes: The deleted files and directories, as a commit reports them.
        mkdirs: The new directories.
    """

    creates: tuple[FileDiff, ...] = ()
    updates: tuple[FileDiff, ...] = ()
    moves: tuple[PathMove, ...] = ()
    deletes: tuple[str, ...] = ()
    mkdirs: tuple[str, ...] = ()

    def lines(self) -> list[str]:
        """One line per change, in the order a model reads them back.

        >>> move = PathMove("~/a.md", "~/b.md", replaces=True)
        >>> ChangesetSummary(moves=(move,)).lines()
        ['- move ~/a.md -> ~/b.md (replaces it)']
        """
        return [
            *(
                f"- move {move.source} -> {move.destination}"
                + (" (replaces it)" if move.replaces else "")
                for move in self.moves
            ),
            *(f"- create {change.path}" for change in self.creates),
            *(f"- update {change.path}" for change in self.updates),
            *(f"- delete {path}" for path in self.deletes),
            *(f"- mkdir {path}" for path in self.mkdirs),
        ]

    @property
    def count(self) -> int:
        """How many changes the summary lists."""
        return len(self.lines())


_NO_NEWLINE = "\n\\ No newline at end of file\n"
_TRUNCATED = "… (diff truncated)\n"


def _lines(text: str) -> list[str]:
    r"""Split *text* after every ``\n`` and nowhere else.

    Unlike ``str.splitlines``, which also breaks at form feeds and Unicode line
    separators, so a diff would show line breaks the file does not have.

    >>> _lines("a\x0cb\nc")
    ['a\x0cb\n', 'c']
    """
    *complete, last = text.split("\n")

    return [f"{line}\n" for line in complete] + ([last] if last else [])


def capped_diff(path: str, old: str | None, new: str, cap: int) -> str:
    r"""A unified diff of one file, cut to *cap* characters.

    A last line without a newline is marked the way ``diff`` marks it, so a
    change that only adds or drops the final newline is visible.  The diff is
    consumed only up to *cap*, and not computed at all when there is no room.

    >>> print(capped_diff("a.md", "x", "x\n", 100), end="")
    --- a.md
    +++ a.md
    @@ -1 +1 @@
    -x
    \ No newline at end of file
    +x
    >>> capped_diff("a.md", None, "x\n", 0)
    '… (diff truncated)\n'
    """
    if cap <= 0:
        return _TRUNCATED

    lines = difflib.unified_diff(
        _lines(old) if old is not None else [],
        _lines(new),
        fromfile="/dev/null" if old is None else path,
        tofile=path,
    )
    parts: list[str] = []
    size = 0

    for line in lines:
        part = line if line.endswith("\n") else f"{line}{_NO_NEWLINE}"
        parts.append(part)
        size += len(part)

        if size > cap:
            return f"{''.join(parts)[:cap]}\n{_TRUNCATED}"

    return "".join(parts)
