"""Turn a sandboxed program's overlay into one changeset, and apply it later.

A program records every change it makes in :class:`~hivegent.tools.workspace_os.WorkspaceOS`
rather than on disk.  The overlay is already a diff of two states, each node a
path as it is once the program ran, so :func:`stage_changes` spells it in the
gateway's declarative terms with no ordering to work out: every source and
basis is a path as it is now, every destination and write target one as it
will be.

A gated root's nodes are entries and directories:

* A directory carried by a rename is one move.
* An entry moves and goes whole: renaming its description or its original
  moves the entry with its ``.assets``, the companion included, and removing
  its original removes the entry, while removing the description alone, or
  moving one part and removing or renaming the other elsewhere, is refused.
* A file written after a rename is the move and a write of the file the move
  carried, and one renamed onto another file replaces it, as on POSIX.
* A removed directory is one delete covering everything below it, and an
  empty directory the program created is created.

A direct root's (``/tmp``) are plain files: a rename within it is a move,
which carries a directory or a binary without rewriting it, a file renamed onto
another one deletes that one, and every other node is a delete, a new
directory, or a whole write.  A rename to or from a gated root was recorded as
a write and a removal.

Every operation carries the stat the file had when the program first touched
it, a direct root's too, so a version landing in between is refused at commit
rather than overwritten, however long a staged changeset waits.

This module names no store and no folder: the agent layer routes every item
to its root and applies the changeset at once, or plans and stages all of it
when any item's root asks, through callbacks, the way every other mutation
tool reaches the workspace.
"""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import Annotated, Literal

from exar.text import plural
from exar.tools import Retry, effect
from exar.tools.batch import tool_return
from pydantic import Field
from pydantic_ai import ToolReturn

from .base import Direct, ToolFunc
from .changes import (
    Changeset,
    ChangesetSummary,
    CreateDir,
    Delete,
    Move,
    Operation,
    Write,
)
from .entries import (
    is_below,
    is_description_file,
    repoint_asset_refs,
    resolve_entry_paths,
    stem_path_from_reference,
)
from .mutations import check_delimited_rows
from .workspace_os import Deleted, Dir, Entry, Node, Ref, Text, WorkspaceOS

__all__ = [
    "AppliedChanges",
    "ApplyChanges",
    "ChangesetIdArg",
    "ChangesetOutcome",
    "CommitChanges",
    "PendingChanges",
    "apply_changes",
    "changes_report",
    "stage_changes",
]


@dataclass(slots=True, frozen=True)
class PendingChanges:
    """A program's workspace changes awaiting ``apply_changes``.

    Attributes:
        changeset_id: What ``apply_changes`` applies them by.
        summary: What the user is asked to approve.
        held: How many changes to a direct root (``/tmp``) wait with them, left
            out of *summary* since nobody approves them.
    """

    changeset_id: str
    summary: ChangesetSummary
    held: int
    status: Literal["pending"] = "pending"


@dataclass(slots=True, frozen=True)
class AppliedChanges:
    """A program's workspace changes, applied at once since nobody had to approve them.

    Attributes:
        reports: The gateway's report of each change.
    """

    reports: tuple[str, ...]
    status: Literal["applied"] = "applied"


type ChangesetOutcome = PendingChanges | AppliedChanges
"""What became of a program's workspace changes."""

CommitChanges = Callable[[Changeset[str]], Awaitable[ChangesetOutcome | None]]
"""Apply a program's changes at once, or stage all of them when any of them asks.

``None`` when the program changed no gated root and nothing was staged.
"""

ApplyChanges = Callable[[str], Awaitable[tuple[str, ...]]]
"""Apply a staged changeset by id, returning the gateway's report of each change."""


def changes_report(changeset: ChangesetOutcome | None) -> str:
    """Tell the model what a changeset did and what is left to do about it."""
    match changeset:
        case None:
            return ""
        case PendingChanges(changeset_id=changeset_id, summary=summary, held=held):
            changes = f"{summary.count} {plural(summary.count, 'change')}"
            lines = "\n".join(summary.lines())
            tmp = (
                "/tmp is unchanged too, since the changeset also holds the "
                f"program's {held} {plural(held, 'change')} there.\n"
                if held
                else ""
            )

            return (
                f"Staged {changes}, and nothing in the workspace has changed yet:\n"
                f"{lines}\n{tmp}Call apply_changes with changeset_id='{changeset_id}' "
                "to apply them, and the user approves them there. Do not make them again "
                "unless that call says the changeset is stale."
            )
        case _:
            count = len(changeset.reports)
            reports = "\n".join(changeset.reports)

            return f"Applied {count} {plural(count, 'change')} to the workspace:\n{reports}"


def _stem(entry: Entry) -> str:
    return entry.search_path.mount.render(stem_path_from_reference(entry.local))


@dataclass(slots=True)
class _Stager:
    """Collects the operations of one overlay's gated half, refusals included."""

    fs: WorkspaceOS
    nodes: dict[str, Node]
    """The overlay's gated half."""
    carried: set[str] = field(default_factory=set)
    """Disk paths a rename carries elsewhere, whose place is therefore free."""
    operations: list[Operation[str]] = field(default_factory=list)
    removed: set[str] = field(default_factory=set)
    """Every disk path a delete takes, companions included."""
    refusals: list[str] = field(default_factory=list)

    def _primaries(self, entry: Entry) -> set[str]:
        """The description and original on disk that make up *entry*'s document."""
        sp = entry.search_path
        paths = resolve_entry_paths(sp.path, entry.local, self.fs.listdir)

        return {
            sp.mount.render(path)
            for path in (paths.description_path, paths.original_path)
            if path is not None and self.fs.base(sp.mount.render(path)) is not None
        }

    def _under(self, key: str) -> Entry | None:
        """The disk file at *key* that stays unless this changeset removes it."""
        under = self.fs.lookup(key, below=True)

        if isinstance(under, Entry) and under.canonical not in self.carried:
            return under

        return None

    def write(
        self,
        path: str,
        content: str,
        base: Entry | None,
        names: tuple[str, str] | None = None,
    ) -> None:
        """Stage *content* at *path* over *base*, the disk file whose text it replaces.

        *names* repoints a moved description's asset references, as the move does.
        """
        old = self.fs.decode(base) if base is not None else None

        if names is not None:
            content = repoint_asset_refs(content, *names)
            old = None if old is None else repoint_asset_refs(old, *names)

        if old == content:
            return

        check_delimited_rows(path, content)

        if base is None:
            self.operations.append(Write(path, content, "create"))
        else:
            self.operations.append(
                Write(path, content, "replace", self.fs.bases.get(base.canonical))
            )

    def delete(self, path: str, parts: set[str]) -> None:
        self.operations.append(Delete(path, self.fs.bases.get(path)))
        self.removed |= parts

    def carry(
        self, entry: Entry, parts: dict[str, str], gone: set[str]
    ) -> dict[str, str]:
        """Stage one move of an entry a rename carried, returning where each part lands.

        *parts* maps every renamed part to its destination, and a part the program
        left alone follows them.
        """
        stem, primaries = _stem(entry), self._primaries(entry)
        stems = {
            stem_path_from_reference(destination) for destination in parts.values()
        }

        if len(stems) != 1 or primaries & gone:
            listed = ", ".join(f"'{part}'" for part in sorted(primaries))
            self.refusals.append(
                f"The document '{stem}' consists of {listed}, which move and go "
                "together: rename them to one new name, or remove the original."
            )

            return {}

        new_stem = stems.pop()
        lands = {
            part: new_stem + part[len(stem) :] for part in primaries | parts.keys()
        }
        source = min(parts, key=lambda path: (not is_description_file(path), path))
        self.operations.append(Move(source, lands[source], self.fs.bases.get(source)))

        return lands

    def remove(self, gone: list[Entry]) -> None:
        """Stage every deletion, a directory covering what lies below it."""
        directories = [entry.canonical for entry in gone if entry.is_dir]
        groups: dict[str, tuple[Entry, set[str]]] = {}

        for entry in gone:
            key = entry.canonical

            if any(is_below(key, directory) for directory in directories):
                continue

            if entry.is_dir:
                self.delete(key, {key})
            else:
                groups.setdefault(_stem(entry), (entry, set()))[1].add(key)

        for entry, parts in groups.values():
            primaries = self._primaries(entry)
            originals = {path for path in primaries if not is_description_file(path)}

            if originals and not parts & originals:
                description, original = min(parts), min(originals)
                self.refusals.append(
                    f"'{description}' is the searchable text of '{original}', so it "
                    f"cannot go on its own: remove '{original}' to delete the whole "
                    f"document, or write '{description}' instead of replacing it."
                )
            else:
                self.delete(min(parts), primaries | parts)

    def classify(
        self,
    ) -> tuple[
        dict[str, tuple[Entry, dict[str, str]]], list[tuple[str, str, Entry | None]]
    ]:
        """Stage the directory moves, returning the file moves by stem and the texts written."""
        fs = self.fs
        moved: dict[str, tuple[Entry, dict[str, str]]] = {}
        texts: list[tuple[str, str, Entry | None]] = []

        for key, node in self.nodes.items():
            origin = node.origin if isinstance(node, Ref | Text) else None
            entry = fs.base(origin) if origin is not None else None

            if origin is not None and entry is None:
                self.refusals.append(f"'{origin}' disappeared while the program ran.")
            elif entry is not None and entry.is_dir:
                self.operations.append(
                    Move(entry.canonical, key, fs.bases.get(entry.canonical))
                )
            elif entry is not None:
                moved.setdefault(_stem(entry), (entry, {}))[1][entry.canonical] = key

            if entry is not None:
                self.carried.add(entry.canonical)

            if isinstance(node, Text):
                texts.append((key, node.content, entry))

        return moved, texts

    def gone(self) -> list[Entry]:
        """The disk entries the program removed or renamed another onto.

        A rename onto a file replaces it, so that file goes like a removed one.
        """
        return [
            under
            for key, node in self.nodes.items()
            if isinstance(node, Deleted | Ref)
            or (isinstance(node, Text) and node.origin)
            if (under := self._under(key)) is not None
        ]

    def write_texts(
        self, texts: list[tuple[str, str, Entry | None]], lands: dict[str, str]
    ) -> None:
        """Stage every text, written where its file lands once a rename carried it."""
        for key, content, entry in texts:
            # Text written over a file is that file's, which a moved entry takes
            # along, and which a removed entry no longer has.
            base = entry or self._under(key)
            base = None if base is None or base.canonical in self.removed else base
            target = lands.get(base.canonical, key) if base is not None else key
            names = None

            if base is not None and is_description_file(target):
                names = PurePosixPath(base.canonical).stem, PurePosixPath(target).stem

            self.write(target, content, base, names)

    def create_dirs(self) -> None:
        """Stage the directories that stay empty."""
        created = [
            key for key, node in self.nodes.items() if not isinstance(node, Deleted)
        ]

        for key, node in self.nodes.items():
            if (
                isinstance(node, Dir)
                and self.fs.lookup(key, below=True) is None
                and not any(is_below(path, key) for path in created)
            ):
                self.operations.append(CreateDir(key))

    def stage(self) -> None:
        moved, texts = self.classify()
        gone = self.gone()
        gone_paths = {entry.canonical for entry in gone}
        lands: dict[str, str] = {}

        for entry, parts in moved.values():
            lands |= self.carry(entry, parts, gone_paths)

        self.remove(
            [entry for entry in gone if entry.is_dir or _stem(entry) not in moved]
        )
        self.write_texts(texts, lands)
        self.create_dirs()


def _direct(fs: WorkspaceOS, nodes: dict[str, Node]) -> list[Operation[str]]:
    """A direct root's nodes as the plain file operations they stand for.

    A node a rename carried here is a move from the disk path it came from, and
    every disk file the program removed or renamed another onto goes, unless a
    rename carries it elsewhere.  Sources and deletes name the disk as it is,
    so a file below a renamed directory is deleted where it lies now.  Each
    carries its basis as a gated one does, and a write the file it replaces,
    or must not find, since a staged changeset may land long after the run.
    """
    bases = fs.bases
    origins = {
        key: node.origin
        for key, node in nodes.items()
        if isinstance(node, Ref | Text) and node.origin is not None
    }
    carried = set(origins.values())
    operations: list[Operation[str]] = [
        Move(origin, key, bases.get(origin)) for key, origin in origins.items()
    ]

    for key, node in nodes.items():
        under = fs.lookup(key, below=True)
        replaced = (
            under.canonical
            if isinstance(under, Entry) and under.canonical not in carried
            else None
        )

        if replaced is not None and (isinstance(node, Deleted) or key in origins):
            operations.append(Delete(replaced, bases.get(replaced)))

        match node:
            case Text(origin=origin):
                read = origin or replaced
                operations.append(
                    Write(key, node.content, "create")
                    if read is None
                    else Write(key, node.content, "replace", bases.get(read))
                )
            case Dir():
                operations.append(CreateDir(key))
            case Ref() | Deleted():
                pass

    return operations


def stage_changes(fs: WorkspaceOS) -> Changeset[str] | None:
    """Spell what a program changed as one changeset, or ``None`` when it changed nothing.

    The overlay is partitioned by its roots' policies: the gated half is staged
    as entries and directories, the direct half as plain files.

    Raises:
        Retry: When the gated changes cannot be applied as one changeset,
            naming every reason at once so one corrected run fixes them all.
    """
    gated: dict[str, Node] = {}
    direct: dict[str, Node] = {}

    for key, node in fs.nodes.items():
        (direct if isinstance(fs.policy(key), Direct) else gated)[key] = node

    stager = _Stager(fs, gated)
    stager.stage()

    if stager.refusals:
        raise Retry("\n".join(stager.refusals))

    operations = (*stager.operations, *_direct(fs, direct))

    return Changeset(operations) if operations else None


ChangesetIdArg = Annotated[
    str,
    Field(description="The `changeset_id` a run_python result staged."),
]


def apply_changes(*, apply: ApplyChanges) -> ToolFunc:
    """The tool that applies a changeset a sandboxed program staged, all or nothing, through *apply*."""

    @effect("write")
    async def apply_changes(changeset_id: ChangesetIdArg) -> ToolReturn[str]:
        """Apply the workspace changes a run_python program staged.

        A program's writes, renames, and deletions are staged rather than made,
        and its result names the changeset.  This applies every one of them at
        once or none, after the user approved them.  Call it once per staged
        changeset rather than re-running the program.  If a document changed
        since the program ran, the changeset is refused and the program has to
        run again to stage a fresh one.
        """
        reports = await apply(changeset_id)
        count = len(reports)
        report = "\n".join(reports)

        return tool_return(
            report, f"Applied {count} {plural(count, 'change')}.\n{report}"
        )

    return apply_changes
