"""The workspace as a copy-on-write filesystem a sandboxed program can open.

Mounting the workspace makes ``open``, ``iterdir``, ``re``, and ``json`` the
equivalents of the read tools, which is what bounds the host functions injected
beside it (:mod:`hivegent.tools.monty`): a mounted equivalent is never one of
them, so what is injected is only what no program can work out for itself,
reaching the database or the network.

A program addresses a document the way everything else does, by its full
workspace path (``~/reports/q1.md``, ``@team/notes.md``), so the path a tool
result spells, a citation carries, and a program opens are one string with no
prefix to add or drop.  The conversation's own folder is ``/tmp``, the spelling
every tool uses for it too, and any other absolute path is refused, since
nothing would keep what a program wrote there.  The run's working directory is
:data:`WORKSPACE_MOUNT`, so Monty resolves a workspace path to one under it
before this filesystem sees it, and a model that has met other sandboxes may
spell that absolute form itself.

Every read routes through :func:`~hivegent.tools.base.resolve_accessible_file`
and :func:`~hivegent.tools.base.entry_visible`, the same seams the read tools
use, which is what keeps the ``DocumentFilter`` a single predicate rather than
gaining a third enforcement surface that could disagree with the other two.
``pydantic_monty.MountDir`` would have been less code and none of that: it maps
a host directory in whole, so it can enforce no filter, decode no legacy
encoding, and hide no ``.assets`` payload.

Writes never reach the disk while the program runs.  A filesystem callback can
neither await the mutation gateway nor stop to ask for approval, so every write,
append, rename, unlink, ``mkdir``, and ``rmdir`` is recorded in an overlay
(:attr:`WorkspaceOS.nodes`) that every later read consults first, so a program
sees its own changes exactly as a filesystem would show them.  Once the program
succeeds the overlay is turned into one changeset
(:func:`hivegent.tools.changeset.stage_changes`), and a program that fails
changes nothing at all.  How each change lands is its root's
:class:`~hivegent.tools.base.CommitPolicy`: a gated root's changes are staged
for approval and count against :class:`ChangesetLimits`, while a direct root's
(``/tmp``) are written straight into its folder.  No one commit carries a file
from one policy to the other, so a rename between them is a write of the text
and a removal.
"""

import errno
import os
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from stat import S_ISDIR, S_ISREG
from typing import Any, NamedTuple, NoReturn, TypeGuard, override

from pydantic_monty import (
    AbstractOS,
    MontyFileHandle,
    OSAccess,
    OsFunction,
    StatResult,
)
from pydantic_monty.os_access import path_from_arg

from ..config import normalize_unicode
from ..converters import BINARY_WRITE_REASON, writes_as_text
from ..entries import (
    ContentStat,
    is_assets_dir,
    is_below,
    is_reserved_path,
    list_names,
    path_key,
    rebase,
)
from ..text import NOT_TEXT_REASON, read_text_file
from .base import (
    DEFAULT_EXCLUDE_DIRS,
    CommitPolicy,
    Direct,
    SearchPath,
    addressable_roots,
    check_read_budget,
    entry_stat,
    entry_visible,
    match_scope,
    policy_of,
    resolve_accessible_file,
    sidecar_hint,
    translate_tool_retry,
)

__all__ = [
    "MOUNT_STUB",
    "WORKSPACE_MOUNT",
    "ChangesetLimits",
    "Deleted",
    "Dir",
    "Entry",
    "Node",
    "Ref",
    "Text",
    "WorkspaceOS",
]

MOUNT_STUB = "from typing import Any\n\ndef open(*args: Any, **kwargs: Any) -> Any: ..."
"""What this mount adds to the interpreter, declared for the type checker.

``open`` reaches :meth:`WorkspaceOS.path_open` rather than a Monty builtin, and
sits in no module the checker can import, so without this line every program
that reads a document is rejected before it runs.  Making the name resolve is
the whole job, and it belongs here because this class is what provides it.

Untyped on purpose.  Monty's own ``pathlib`` stub gives ``Path.open()`` the type
``Unknown``, so there is no file type to reuse, and a spelled-out handle would
be one this repo invented against a runtime it does not own: an earlier attempt
was already wrong three ways, refusing an ``encoding=`` keyword, a third
positional argument, and ``seek``, each of which the interpreter accepts.  A
stub that rejects a working program is worse than one that checks nothing.
Never shown to the model, which knows what ``open`` is.
"""

WORKSPACE_MOUNT = PurePosixPath("/workspace")
"""The run's working directory, and so the absolute root of every workspace path.

Monty resolves a relative path against the working directory before it reaches
the ``os`` handler, so ``~/reports/q1.md`` arrives as
``/workspace/~/reports/q1.md``.  A listing and a refusal still spell a document
the way every tool result does, so the model never meets this form unless it
reaches for it, as a model carrying another sandbox's habits does.
"""

type VirtualPath = PurePosixPath | MontyFileHandle
"""How a path reaches this filesystem.

A ``MontyFileHandle`` arrives when the program opened the file rather than
reading it in one call; it is a plain data holder naming the same path, so
every method funnels through :meth:`WorkspaceOS._canonical` and none of them
has to care which of the two it was handed.
"""


class Entry(NamedTuple):
    """A resolved document or directory on disk, with its one stat."""

    search_path: SearchPath
    local: str
    absolute: Path
    stat: os.stat_result

    @property
    def canonical(self) -> str:
        """The path as tools spell it, prefix included."""
        return self.search_path.prefixed(self.local)

    @property
    def is_dir(self) -> bool:
        """Whether the entry is a directory."""
        return S_ISDIR(self.stat.st_mode)

    @property
    def basis(self) -> ContentStat:
        """The stat a change of this file is checked against when it commits."""
        return ContentStat.of(self.stat)


@dataclass(slots=True)
class Text:
    """A file whose content the program wrote.

    Kept as chunks so a program building a file one ``append`` at a time pays
    for each line once rather than for the whole file on every call.

    Attributes:
        chunks: The content, in order.
        origin: The disk file a rename carried here before it was written,
            whose entry the write continues.
    """

    chunks: list[str]
    origin: str | None = None
    length: int = 0

    @property
    def content(self) -> str:
        """The whole text, joined once and kept joined."""
        if len(self.chunks) > 1:
            self.chunks[:] = ["".join(self.chunks)]

        return self.chunks[0] if self.chunks else ""


@dataclass(slots=True, frozen=True)
class Ref:
    """A file or directory a rename carried here, unchanged from *origin* on disk."""

    origin: str


@dataclass(slots=True, frozen=True)
class Deleted:
    """A path that exists on disk and no longer does in the program's view."""


@dataclass(slots=True, frozen=True)
class Dir:
    """A directory the program created, which holds only what it puts there."""


type Node = Text | Ref | Deleted | Dir
"""What the overlay records for one canonical path."""

type _View = Text | Dir | Entry | None
"""What a path is in the program's view: staged text, a new directory, the disk, or nothing."""


def _is_dir(view: _View) -> TypeGuard[Dir | Entry]:
    return isinstance(view, Dir) or (isinstance(view, Entry) and view.is_dir)


@dataclass(slots=True, frozen=True)
class ChangesetLimits:
    """What one program may stage, checked as it records each change.

    Attributes:
        max_operations: Paths one program may create, change, move, or delete.
        max_deletes: Of those, how many it may delete.
        max_chars: Characters it may write in total, across every file.
    """

    max_operations: int
    max_deletes: int
    max_chars: int


def _root_hint(paths: tuple[SearchPath, ...]) -> str:
    """Name the roots in *paths*, which is what a path here has to lead with.

    :func:`~hivegent.tools.base.workspace_root_hint` in the sandbox's own
    words, over the roots that function names.
    """
    roots = addressable_roots(paths)
    if not roots:
        return ""

    return (
        f" A path leads with {', '.join(roots)}, the same path every tool "
        "result spells."
    )


def _text(path: VirtualPath) -> str:
    """The path as one string, whichever of the two forms it arrived in.

    The fold to NFC is the same one :func:`~hivegent.tools.base.resolve_search_path`
    applies to every other inbound tool path, and it has to happen here too,
    since macOS hands out decomposed filenames while a program can only spell
    precomposed ones.
    """
    return normalize_unicode(str(path_from_arg(path)))


def _ancestors(key: str) -> list[str]:
    """Every directory above a canonical path, nearest first.

    >>> _ancestors("~/a/b.md"), _ancestors("/tmp/a")
    (['~/a', '~'], ['/tmp'])
    """
    return [str(parent) for parent in PurePosixPath(key).parents if parent.name]


def _is_path(arg: object) -> TypeGuard[VirtualPath]:
    """Whether one dispatched argument is a path rather than a mode or a flag."""
    return isinstance(arg, PurePosixPath | MontyFileHandle)


def _os_error[E: OSError](kind: type[E], code: int, name: str) -> E:
    """The error the OS raises for *code* on *name*, worded as it words it.

    >>> str(_os_error(IsADirectoryError, errno.EISDIR, "~/a"))
    "[Errno 21] Is a directory: '~/a'"
    """
    return kind(code, os.strerror(code), name)


@dataclass(slots=True)
class WorkspaceOS(AbstractOS):
    """Routes the mounted roots to the overlay and the disk, and the rest to ``inner``.

    ``inner`` answers what names no path, the environment and the clocks.
    """

    paths: tuple[SearchPath, ...]
    """Every root the read tools span, with their filters applied."""

    inner: OSAccess
    """The run's own environment and clocks."""

    limits: ChangesetLimits
    """What the gated roots may be changed by one program."""

    writable: tuple[SearchPath, ...] = ()
    """Roots this run may change, narrower than the roots above.

    A program reads every workspace the user can see and changes only one they
    may mutate, plus ``/tmp``, and only ``/tmp`` in a mode that may not write the
    workspace, which is what refuses every such change as it is made.
    """

    max_document_chars: int = 5_000_000
    """Cap on one document, read or written.

    Per document rather than per run, because a decoded document is what the
    host actually holds: it is read whole and decoded in one go, then handed to
    the interpreter and dropped, so at most one is alive at a time however many
    a program opens.  Measured over a 2000-document, 100 MB workspace, reading
    every one of them moved the server's peak RSS by a megabyte.
    """

    nodes: dict[str, Node] = field(default_factory=dict)
    """The program's changes, by canonical path, consulted before the disk."""

    bases: dict[str, ContentStat] = field(default_factory=dict)
    """The stat each disk file had when the program first touched it.

    The basis its change commits against, so a version landing between the
    program's read and the commit is refused rather than overwritten.  A stat
    rather than a content hash, since every read would otherwise hash what it
    decodes, and a program walking the workspace reads far more than it
    changes.
    """

    written: int = 0
    touched: set[str] = field(default_factory=set)
    deletes: int = 0
    listings: dict[Path, Sequence[str]] = field(default_factory=dict)
    """The disk's directory listings, read once per run."""
    entries: dict[str, Entry | None] = field(default_factory=dict)
    """What :meth:`base` found at each canonical path, read once per run.

    The disk is a snapshot for the program, which changes it only once it ran.
    """

    # -- addressing -----------------------------------------------------

    def _canonical(self, path: VirtualPath) -> str | None:
        """The canonical workspace path a virtual one names, or ``None`` if outside.

        Every path arrives absolute, a relative one resolved against
        :data:`WORKSPACE_MOUNT` by Monty, so the canonical path is what follows
        that prefix, and one under a root whose prefix is absolute (``/tmp``)
        is already canonical.  ``"."`` is the mount itself, a directory listing
        the workspaces rather than a path any of them claims.
        """
        pure = PurePosixPath(_text(path))

        if pure.is_relative_to(WORKSPACE_MOUNT):
            return str(pure.relative_to(WORKSPACE_MOUNT))

        return str(pure) if match_scope(self.paths, str(pure)) is not None else None

    def policy(self, key: str) -> CommitPolicy:
        """How a change to the canonical *key* commits, which is its root's to say."""
        return policy_of(self.paths, key)

    def _mounted(self, arg: object) -> bool:
        """Whether one dispatched argument addresses the workspace.

        Typed rather than duck-tested so a mode string (``open(path, "w")``)
        can never be read as a path, and so an argument that is no path at all,
        a flag or a timezone, answers no rather than raising.
        """
        return _is_path(arg) and self._canonical(arg) is not None

    def _at_root(self, path: VirtualPath) -> bool:
        """Whether *path* is the mount itself, the directory listing the workspaces."""
        return self._canonical(path) == "."

    def _named(self, path: VirtualPath) -> str:
        """How a refusal spells *path*: the canonical name every tool result uses."""
        return self._canonical(path) or _text(path)

    def _missing(self, path: VirtualPath) -> NoReturn:
        """Refuse a path that resolves to nothing, as the program spelled it.

        A path leading with no workspace root at all (``/workspace/notes.md``,
        the mount prefix and a document name with the scope dropped in between)
        is the one miss the failure itself cannot correct, so it ends in
        :meth:`_root_hint` the way every tool argument ends in
        :func:`~hivegent.tools.base.workspace_root_hint`.
        """
        canonical = self._canonical(path)
        known = canonical is not None and match_scope(self.paths, canonical) is not None
        hint = "" if known else _root_hint(self.paths)
        error = _os_error(FileNotFoundError, errno.ENOENT, self._named(path))

        raise FileNotFoundError(f"{error}.{hint}") if hint else error

    # -- routing --------------------------------------------------------

    @override
    def dispatch(
        self,
        function_name: OsFunction,
        args: tuple[Any, ...],
        kwargs: dict[str, Any] | None = None,
        *,
        is_async: bool = False,
    ) -> Any:
        """Send one operation to the mount, refuse it, or hand it to ``inner``.

        The one place that is decided, which is the seam ``AbstractOS`` offers
        for exactly this.  An operation naming no path at all, the environment
        or entropy, belongs to ``inner``.  A path outside every root is refused
        rather than kept in memory: whatever a program wrote there would be
        gone after the call, which is the state a later call then looks for.
        A rename with one end mounted is the mount's, which refuses the other.
        """
        if any(self._mounted(arg) for arg in args):
            return super().dispatch(function_name, args, kwargs, is_async=is_async)

        if path := next(filter(_is_path, args), None):
            raise PermissionError(
                f"'{_text(path)}' is outside this run's filesystem.{_root_hint(self.paths)}"
            )

        return self.inner.dispatch(function_name, args, kwargs, is_async=is_async)

    def _root(self, canonical: str) -> SearchPath | None:
        """The search path *canonical* names bare, if it names one at all."""
        match = match_scope(self.paths, canonical)

        return match[0] if match is not None and not match[1] else None

    def base(self, canonical: str) -> Entry | None:
        """What the disk holds at a canonical path, as the read tools would see it.

        A bare scope root resolves to the search path itself with an empty
        local name.  ``.assets`` payloads are hidden in whole, the directory
        included: their files are the entry's to manage, and no program can
        read one anyway.
        """
        if canonical not in self.entries:
            self.entries[canonical] = self._base(canonical)

        return self.entries[canonical]

    def _base(self, canonical: str) -> Entry | None:
        root = self._root(canonical)
        resolved = (
            (root, "", root.path)
            if root is not None
            else resolve_accessible_file(self.paths, canonical, self.listdir)
        )
        if resolved is None:
            return None

        sp, local, absolute = resolved

        if local and (
            not entry_visible(sp, local, DEFAULT_EXCLUDE_DIRS) or is_reserved_path(local)
        ):
            return None

        st = entry_stat(absolute)

        return None if st is None else Entry(sp, local, absolute, st)

    def listdir(self, directory: Path) -> Sequence[str]:
        """The names *directory* holds on disk, listed once per run."""
        if directory not in self.listings:
            self.listings[directory] = list_names(directory)

        return self.listings[directory]

    def _names(self, directory: Path) -> list[str]:
        """The names *directory* holds in the program's view, the overlay's spelling first.

        What a spelling is matched against, so on a case-insensitive workspace
        ``a.md`` opens the ``A.md`` the program created, and one file keeps one
        key in the overlay.
        """
        parent = None

        for sp in self.paths:
            if directory.is_relative_to(sp.path):
                local = directory.relative_to(sp.path).as_posix()
                parent = sp.prefixed("" if local == "." else local)
                break

        live = [
            PurePosixPath(key).name
            for key, node in self.nodes.items()
            if not isinstance(node, Deleted) and str(PurePosixPath(key).parent) == parent
        ]

        return [*live, *self.listdir(directory)]

    def _key(self, path: VirtualPath) -> str | None:
        """The normalized canonical path a read names, or ``None`` for nothing."""
        canonical = self._canonical(path)

        if canonical is None or canonical == ".":
            return None

        if self._root(canonical) is not None:
            return canonical

        resolved = resolve_accessible_file(self.paths, canonical, self._names)

        return None if resolved is None else resolved[0].prefixed(resolved[1])

    def lookup(self, key: str, *, below: bool = False) -> _View:
        """What a canonical path is in the program's view.

        The nearest recorded node wins: one at the path itself, else one at an
        ancestor, where a renamed directory maps the path back onto the disk
        under its origin, and anything else hides it.  *below* skips a node at
        the path itself, which is what the disk shows there through its
        ancestors.
        """
        candidates = _ancestors(key) if below else [key, *_ancestors(key)]

        for candidate in candidates:
            node = self.nodes.get(candidate)

            if node is None:
                continue

            if candidate == key:
                match node:
                    case Ref(origin=origin):
                        return self.base(origin)
                    case Deleted():
                        return None
                    case Text() | Dir():
                        return node

            if isinstance(node, Ref):
                return self.base(rebase(key, candidate, node.origin))

            return None

        return self.base(key)

    def _view(self, path: VirtualPath) -> _View:
        key = self._key(path)

        return None if key is None else self.lookup(key)

    # -- reads ----------------------------------------------------------

    @override
    def path_exists(self, path: PurePosixPath) -> bool:
        return self._at_root(path) or self._view(path) is not None

    @override
    def path_is_file(self, path: PurePosixPath) -> bool:
        view = self._view(path)

        return isinstance(view, Text) or (
            isinstance(view, Entry) and S_ISREG(view.stat.st_mode)
        )

    @override
    def path_is_dir(self, path: PurePosixPath) -> bool:
        return self._at_root(path) or _is_dir(self._view(path))

    @override
    def path_is_symlink(self, path: PurePosixPath) -> bool:
        # `entry_stat` reports a symlink as absent rather than following it, so
        # the mount never has one to admit to.
        return False

    @override
    def path_iterdir(self, path: PurePosixPath) -> list[PurePosixPath]:
        if self._at_root(path):
            roots = (PurePosixPath(sp.prefixed("")) for sp in self.paths)

            return [root for root in roots if not root.is_absolute()]

        key = self._key(path)
        view = None if key is None else self.lookup(key)

        if key is None or view is None:
            self._missing(path)

        if not _is_dir(view):
            raise _os_error(NotADirectoryError, errno.ENOTDIR, self._named(path))

        # Relative, as every tool result spells it, and resolved back under the
        # mount by the working directory when the program opens one.  Sorted,
        # because Monty cannot compare two `Path` values, so a program that
        # wants an order has no way to impose one on what it is handed.
        return [PurePosixPath(key, name) for name in sorted(self._children(key, view))]

    def _children(self, key: str, view: Dir | Entry) -> set[str]:
        """The names a directory holds in the program's view: the disk's, then the overlay's."""
        names: set[str] = set()

        if isinstance(view, Entry):
            sp, local = view.search_path, view.local

            with os.scandir(view.absolute) as children:
                names.update(
                    child.name
                    for child in children
                    if not (is_assets_dir(child.name) and child.is_dir())
                    and entry_visible(
                        sp, str(PurePosixPath(local, child.name)), DEFAULT_EXCLUDE_DIRS
                    )
                )

        for candidate, node in self.nodes.items():
            pure = PurePosixPath(candidate)

            if str(pure.parent) != key:
                continue

            if isinstance(node, Deleted):
                names.discard(pure.name)
            else:
                names.add(pure.name)

        return names

    def _holds_unseen(self, key: str, view: Dir | Entry) -> bool:
        """Whether the disk directory still holds a file the program cannot see.

        A filtered document, an excluded directory, or anything a filter hides
        is absent from :meth:`_children`, so the directory looks empty, while
        deleting it would take them along.  An ``.assets`` payload is the one
        hidden child that goes with its entry, which the program removed
        already if the directory looks empty.
        """
        if not isinstance(view, Entry):
            return False

        with os.scandir(view.absolute) as children:
            return any(
                not (is_assets_dir(child.name) and child.is_dir())
                and not isinstance(self.nodes.get(f"{key}/{child.name}"), Deleted)
                for child in children
            )

    @override
    def path_stat(self, path: PurePosixPath) -> StatResult:
        if self._at_root(path):
            return StatResult.dir_stat()

        match self._view(path):
            case None:
                self._missing(path)
            case Text() as text:
                return StatResult.file_stat(size=len(text.content.encode("utf-8")))
            case Dir():
                return StatResult.dir_stat()
            case Entry(stat=st):
                if S_ISDIR(st.st_mode):
                    return StatResult.dir_stat(mtime=st.st_mtime)

                return StatResult.file_stat(size=st.st_size, mtime=st.st_mtime)

    @override
    def path_open(self, path: PurePosixPath, mode: str) -> MontyFileHandle:
        """Answer ``open(path, mode)`` for the mount.

        A handle is a data holder naming the path, so all this owes is the
        open-time effect the mode asks for: a read proves the file is there
        before the program starts reading it, a write truncates through the same
        gate ``write_text`` uses, and an append creates the file when missing.
        """
        handle = MontyFileHandle(str(path), mode)
        if handle.binary:
            self._refuse_bytes(self._named(path))

        action = handle.mode[0]

        if action == "r":
            if self.path_is_dir(path):
                raise _os_error(IsADirectoryError, errno.EISDIR, self._named(path))

            if not self.path_is_file(path):
                self._missing(path)

            return handle

        _ = self._write(path, "", append=action == "a")

        return handle

    @override
    def path_read_text(self, path: PurePosixPath | MontyFileHandle) -> str:
        view = self._view(path)

        if isinstance(view, Text):
            return view.content

        if view is None and not self._at_root(path):
            self._missing(path)

        if not isinstance(view, Entry) or view.is_dir:
            raise _os_error(IsADirectoryError, errno.EISDIR, self._named(path))

        text = self.decode(self._remember(view))

        if text is None:
            # The exception types are the mount's own, since a `ToolRetry`
            # reaches the program as a bare `Exception`, but the sentence
            # is every reader's, sidecar included.
            raise ValueError(
                f"'{view.canonical}' {NOT_TEXT_REASON}.{sidecar_hint(view.canonical)}"
            )

        return text

    @override
    def path_read_bytes(self, path: PurePosixPath | MontyFileHandle) -> bytes:
        self._refuse_bytes(self._named(path))

    def _refuse_bytes(self, canonical: str) -> NoReturn:
        """Refuse a byte channel, whatever the document turns out to be.

        Text is the only thing a program can do anything with, and answering
        this before the file is even resolved keeps one answer to "what can
        this run open" rather than two.  A document with no text form at all
        is the read_binary_document tool's, which the model reaches from
        outside a program.
        """
        raise ValueError(
            f"'{canonical}' can only be read and written as text, since the "
            "sandbox serves no bytes from the workspace. Use text mode, or the "
            "read_binary_document tool for a document with no text form."
        )

    def decode(self, entry: Entry) -> str | None:
        """Decode one disk file, bounded before and after, or ``None`` for a binary.

        :func:`check_read_budget` bounds it by size, which is what keeps an
        oversized file out of memory in the first place, but a size is only an
        upper bound on a character count, so the exact length is checked once
        the text is in hand.
        """
        with translate_tool_retry(MemoryError):
            check_read_budget(entry.canonical, entry.stat.st_size, self.max_document_chars)

        decoded = read_text_file(entry.absolute)

        if decoded is None:
            return None

        if len(decoded.text) > self.max_document_chars:
            raise MemoryError(
                f"'{entry.canonical}' is too large to read here ({len(decoded.text)} "
                f"characters, and one document may hold at most "
                f"{self.max_document_chars})."
            )

        return decoded.text

    # -- writes, which the overlay records -------------------------------

    def _remember(self, entry: Entry) -> Entry:
        """Keep the stat *entry* has when the program first touches it, its change's basis."""
        _ = self.bases.setdefault(entry.canonical, entry.basis)

        return entry

    def _target(self, path: VirtualPath) -> str:
        """The canonical path a change may touch, or a refusal naming why not.

        Resolved against the writable span rather than the mounted one, which
        is wider, and on the canonical local path rather than the spelling it
        was addressed by, so neither a ``..`` segment nor a symlink can carry a
        path into a workspace the user may only read or into an ``.assets``
        payload.
        """
        canonical = self._named(path)
        resolved = (
            None
            if canonical == "."
            else resolve_accessible_file(self.writable, canonical, self._names)
        )

        if resolved is None:
            roots = ", ".join(addressable_roots(self.writable)) or "nothing"
            raise PermissionError(
                f"'{canonical}' cannot be changed by this run, which may change {roots}."
            )

        sp, local, _absolute = resolved

        if is_reserved_path(local):
            raise PermissionError(
                f"'{canonical}' lies in an `.assets` directory, which belongs to "
                "its document and follows it when the document moves or goes."
            )

        return sp.prefixed(local)

    def _charge(self, key: str, *, deleting: bool = False) -> None:
        """Count one more changed path against the run's limits, which only a gated root has."""
        if isinstance(self.policy(key), Direct):
            return

        self.touched.add(key)

        if len(self.touched) > self.limits.max_operations:
            raise PermissionError(
                f"Changing '{key}' takes this program past the "
                f"{self.limits.max_operations} paths one run may change. Change "
                "fewer at once and run again for the rest."
            )

        if deleting:
            self.deletes += 1

            if self.deletes > self.limits.max_deletes:
                raise PermissionError(
                    f"Removing '{key}' takes this program past the "
                    f"{self.limits.max_deletes} deletions one run may make."
                )

    def _make_parents(self, key: str) -> None:
        """Create every missing directory above *key*, the way the gateway will."""
        for parent in _ancestors(key):
            view = self.lookup(parent)

            if _is_dir(view):
                return

            if view is not None:
                raise _os_error(NotADirectoryError, errno.ENOTDIR, parent)

            self._new_dir(parent)

    def _new_dir(self, key: str) -> None:
        if isinstance(self.nodes.get(key), Deleted):
            underlying = self.lookup(key, below=True)

            if isinstance(underlying, Entry) and not underlying.is_dir:
                raise FileExistsError(
                    f"'{key}' was a document this program removed, and a directory "
                    "cannot replace it in the same run."
                )

        self.nodes[key] = Dir()

    def _vacate(self, key: str) -> None:
        """Remove *key* from the view, recording a deletion only where the disk has it."""
        _ = self.nodes.pop(key, None)

        if self.lookup(key, below=True) is not None:
            self.nodes[key] = Deleted()

    def _write(self, path: VirtualPath, data: str, *, append: bool) -> int:
        key = self._target(path)
        node = self.nodes.get(key)
        view = self.lookup(key)

        match view:
            case Text():
                pass
            case Entry() if not view.is_dir:
                current = self.decode(self._remember(view))

                if current is None:
                    raise ValueError(
                        f"'{key}' {NOT_TEXT_REASON}, so the sandbox cannot change "
                        "it. The user can upload a replacement."
                    )

                view = Text([current], length=len(current))
            case None:
                if not writes_as_text(key):
                    raise ValueError(f"'{key}' {BINARY_WRITE_REASON}.")

                view = Text([])
            case _:
                raise _os_error(IsADirectoryError, errno.EISDIR, key)

        length = view.length + len(data) if append else len(data)

        if length > self.max_document_chars:
            raise MemoryError(
                f"'{key}' would hold {length} characters, and one document may "
                f"hold at most {self.max_document_chars}."
            )

        self.written += len(data)

        if self.written > self.limits.max_chars:
            raise MemoryError(
                f"Writing '{key}' takes this program past the "
                f"{self.limits.max_chars} characters one run may write."
            )

        if not isinstance(node, Text):
            self._charge(key)
            self._make_parents(key)

        if append:
            view.chunks.append(data)
        else:
            view.chunks[:] = [data]

        view.length = length

        if view is not node:
            origin = node.origin if isinstance(node, Ref) else None
            self.nodes[key] = Text(view.chunks, origin, view.length)

        return len(data)

    @override
    def path_write_text(self, path: PurePosixPath | MontyFileHandle, data: str) -> int:
        return self._write(path, data, append=False)

    @override
    def path_write_bytes(
        self, path: PurePosixPath | MontyFileHandle, data: bytes
    ) -> int:
        self._refuse_bytes(self._named(path))

    @override
    def path_append_text(self, path: PurePosixPath | MontyFileHandle, data: str) -> int:
        return self._write(path, data, append=True)

    @override
    def path_append_bytes(
        self, path: PurePosixPath | MontyFileHandle, data: bytes
    ) -> int:
        self._refuse_bytes(self._named(path))

    @override
    def path_mkdir(self, path: PurePosixPath, parents: bool, exist_ok: bool) -> None:
        key = self._target(path)
        view = self.lookup(key)

        if view is not None:
            if exist_ok and _is_dir(view):
                return

            raise _os_error(FileExistsError, errno.EEXIST, key)

        above = _ancestors(key)

        if not parents and above and self.lookup(above[0]) is None:
            raise _os_error(FileNotFoundError, errno.ENOENT, above[0])

        self._make_parents(key)
        self._new_dir(key)

    @override
    def path_unlink(self, path: PurePosixPath) -> None:
        key = self._target(path)
        view = self.lookup(key)

        if view is None:
            self._missing(path)

        if _is_dir(view):
            raise _os_error(IsADirectoryError, errno.EISDIR, key)

        if isinstance(view, Entry):
            _ = self._remember(view)

        node = self.nodes.get(key)
        self._charge(
            key,
            deleting=isinstance(view, Entry)
            or (isinstance(node, Text) and node.origin is not None),
        )
        self._vacate(key)

    @override
    def path_rmdir(self, path: PurePosixPath) -> None:
        key = self._target(path)
        view = self.lookup(key)

        if view is None:
            self._missing(path)

        if not _is_dir(view):
            raise _os_error(NotADirectoryError, errno.ENOTDIR, key)

        if self._children(key, view) or self._holds_unseen(key, view):
            raise _os_error(OSError, errno.ENOTEMPTY, key)

        if isinstance(view, Entry):
            self._charge(key, deleting=True)

        self._vacate(key)

    @override
    def path_rename(self, path: PurePosixPath, target: PurePosixPath) -> None:
        """Record a rename, which carries the disk file rather than copying its bytes.

        So a binary document moves as freely as a text one.  A file renamed onto
        another one replaces it, as on any POSIX filesystem, while a directory
        neither replaces nor is replaced.  On a case-insensitive workspace both
        spellings of a case-only rename name the source, so the name the
        program spelled is the one it gets.  A rename touching a direct root
        is a copy, see :meth:`_copy`.
        """
        if not (self._mounted(path) and self._mounted(target)):
            raise PermissionError(
                f"'{self._named(path)}' cannot be renamed outside this run's "
                f"filesystem.{_root_hint(self.paths)}"
            )

        source, destination = self._target(path), self._target(target)

        if any(isinstance(self.policy(end), Direct) for end in (source, destination)):
            self._copy(path, target, source, destination)

            return

        view = self.lookup(source)

        if view is None:
            self._missing(path)

        replaced = None

        if destination == source:
            name = PurePosixPath(_text(target)).name
            current = PurePosixPath(source).name

            if name == current or path_key(name, folded=True) != path_key(
                current, folded=True
            ):
                return

            destination = str(PurePosixPath(source).with_name(name))
        else:
            replaced = self.lookup(destination)

        is_dir = _is_dir(view)

        if is_dir and is_below(destination, source):
            raise OSError(errno.EINVAL, "Cannot move a directory into itself", source)

        if replaced is not None and (is_dir or _is_dir(replaced)):
            raise FileExistsError(
                errno.EEXIST,
                "File exists, and a directory neither replaces nor is replaced by "
                "a rename. Pick a free name",
                destination,
            )

        self._check_rename(source, destination, is_dir=is_dir)
        node = self.nodes.get(source)
        origin = self._origin(source, node, view)

        for entry in (view, replaced):
            if isinstance(entry, Entry) and not entry.is_dir:
                _ = self._remember(entry)

        if not isinstance(node, Text | Ref):
            self._charge(source)

        if replaced is not None:
            self._charge(destination, deleting=isinstance(replaced, Entry))

        if is_dir:
            for key in [key for key in self.nodes if is_below(key, source)]:
                self.nodes[rebase(key, source, destination)] = self.nodes.pop(key)

        self._vacate(source)
        _ = self.nodes.pop(destination, None)
        # Back where it came from, a file is the disk's again, or a write in place.
        home = origin == destination

        match node:
            case Text():
                node.origin = None if home else origin
                self.nodes[destination] = node
            case Dir():
                self.nodes[destination] = node
            case _ if origin is not None and not home:
                self.nodes[destination] = Ref(origin)
            case _:
                pass

        self._make_parents(destination)

    def _origin(self, source: str, node: Node | None, view: Text | Dir | Entry) -> str | None:
        """The disk path whose entry a rename of *source* carries, if any."""
        match node:
            case Text(origin=None):
                underlying = self.lookup(source, below=True)

                return underlying.canonical if isinstance(underlying, Entry) else None
            case Text(origin=origin) | Ref(origin=origin):
                return origin
            case Dir():
                return None
            case _:
                return view.canonical if isinstance(view, Entry) else None

    def _copy(
        self, path: PurePosixPath, target: PurePosixPath, source: str, destination: str
    ) -> None:
        """Rename a file to or from a direct root as a write of its text and a removal.

        A direct root holds files rather than entries and commits apart from
        the gated ones, so no move carries a file between them: the text lands
        at *destination* and *source* goes, each the way its own root commits,
        which leaves a removal from a workspace to the approval it always asks.
        """
        if _is_dir(self.lookup(source)):
            raise _os_error(IsADirectoryError, errno.EISDIR, source)

        if source != destination:
            _ = self._write(target, self.path_read_text(path), append=False)
            self.path_unlink(path)

    @staticmethod
    def _check_rename(source: str, destination: str, *, is_dir: bool) -> None:
        """Refuse a rename that would change a document's extension."""
        if not is_dir and PurePosixPath(source).suffix != PurePosixPath(destination).suffix:
            raise PermissionError(
                f"'{source}' cannot be renamed to '{destination}': a document "
                "keeps its extension when it moves. To change its format, write "
                "the converted text to a new path and remove the old one."
            )

    # -- the two answers that are about the path and not the file --------

    @override
    def path_absolute(self, path: PurePosixPath) -> str:
        return str(path)

    @override
    def path_resolve(self, path: PurePosixPath) -> str:
        return str(path)

    # `AbstractOS` declares these two abstract, so they are implemented rather
    # than left to the routing above, which already sends every operation that
    # names no path to the run's own filesystem.
    @override
    def get_environ(self) -> dict[str, str]:
        return self.inner.get_environ()

    @override
    def getenv(self, key: str, default: str | None = None) -> str | None:
        return self.inner.getenv(key, default)
