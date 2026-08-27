"""The workspace as a read-only filesystem a sandboxed program can open.

A ``run_python`` program used to see private copies of the files the model had
named in advance, which meant a program could not open a path it discovered
while running: the model had to know every answer's location before writing the
question.  Mounting the workspace instead makes ``open``, ``iterdir``, ``re``,
and ``json`` the equivalents of the read tools, which is why nothing at all is
injected beside it: a host function would be a second way to do what the mount
already does, and ranking a chunk against a question is a ``search`` call the
model makes before it writes the program.

Every operation routes through :func:`~hivegent.tools.base.resolve_accessible_file`
and :func:`~hivegent.tools.base.entry_visible`, the same seams the read tools
use, which is what keeps the ``DocumentFilter`` a single predicate rather than
gaining a third enforcement surface that could disagree with the other two.
``pydantic_monty.MountDir`` would have been less code and none of that: it maps
a host directory in whole, so it can enforce no filter, decode no legacy
encoding, and hide no ``.assets`` payload.

The mount is read-only but for one exception, ``.scratch/``, which is content
rather than a document: no ``documents`` row, no chunking, no projection, and
no notification, so a write there is a file written and nothing else, and it
needs neither the async mutation gateway a filesystem callback cannot await nor
the approval a running program cannot stop to ask for.  A document is written
the one way a human can answer for in advance, by being named as the call's
``output_path``.  Nothing else about the workspace changes while a program
runs, so what the program sees and what the call commits cannot disagree.
"""

import os
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from stat import S_ISDIR, S_ISREG
from typing import Any, NamedTuple, NoReturn, override

from pydantic_monty import (
    AbstractOS,
    MontyFileHandle,
    OSAccess,
    OsFunction,
    StatResult,
)

from ..config import normalize_unicode
from ..entries import is_scratch_path
from ..text import NOT_TEXT_REASON, read_text_file
from .base import (
    DEFAULT_EXCLUDE_DIRS,
    SearchPath,
    check_read_budget,
    entry_stat,
    entry_visible,
    match_scope,
    resolve_accessible_file,
    sidecar_hint,
    translate_tool_retry,
)

__all__ = [
    "WORKSPACE_MOUNT",
    "WorkspaceOS",
]

WORKSPACE_MOUNT = PurePosixPath("/workspace")
"""Where the workspace appears inside the sandbox.

A canonical path keeps its scope prefix under it, so ``~/reports/q1.md`` is
``/workspace/~/reports/q1.md`` and the path a program prints is the path every
tool result spells, minus one constant prefix.
"""

_MOUNT_ROOT = str(WORKSPACE_MOUNT)
_MOUNT_PREFIX = f"{_MOUNT_ROOT}/"

type VirtualPath = PurePosixPath | str | MontyFileHandle
"""How a path reaches this filesystem.

A ``MontyFileHandle`` arrives when the program opened the file rather than
reading it in one call; it is a plain data holder naming the same path, so
every method funnels through :func:`_canonical` and none of them has to care
which of the three it was handed.
"""


class Entry(NamedTuple):
    """A resolved mounted document, with the one stat every caller asks about."""

    search_path: SearchPath
    local: str
    absolute: Path
    stat: os.stat_result


def _canonical(path: VirtualPath) -> str | None:
    """The canonical workspace path a virtual one names, or ``None`` if outside.

    ``"."`` is the mount root itself, which is a directory listing the
    workspaces rather than a path any of them claims.

    A string test rather than ``PurePosixPath.is_relative_to``, which rebuilds
    the path twice and scans its parents: this runs once per filesystem
    operation and a walk performs one per entry.

    The fold to NFC is the same one :func:`~hivegent.tools.base.resolve_search_path`
    applies to every other inbound tool path, and it has to happen here too,
    since macOS hands out decomposed filenames while a program can only spell
    precomposed ones.
    """
    located = path.path if isinstance(path, MontyFileHandle) else path
    text = normalize_unicode(str(located))
    if text == _MOUNT_ROOT:
        return "."

    return text[len(_MOUNT_PREFIX) :] if text.startswith(_MOUNT_PREFIX) else None


def _mounted(arg: object) -> bool:
    """Whether one dispatched argument addresses the workspace.

    Typed rather than duck-tested so a mode string (``open(path, "w")``) can
    never be read as a path, and so an argument that is no path at all -- a
    flag, a timezone -- answers no rather than raising.
    """
    return (
        isinstance(arg, PurePosixPath | MontyFileHandle) and _canonical(arg) is not None
    )


def _at_root(path: VirtualPath) -> bool:
    """Whether *path* is the mount itself, the directory listing the workspaces."""
    return _canonical(path) == "."


def _named(path: VirtualPath) -> str:
    """How a refusal spells *path*: the canonical name every tool result uses."""
    return _canonical(path) or str(path)


@dataclass(slots=True)
class WorkspaceOS(AbstractOS):
    """Routes the workspace mount to real documents and the rest to ``inner``.

    ``inner`` owns only what the run invents: ``/tmp``, the declared output,
    and whatever a program parks in either.  The mount serves the workspace off
    disk as it lies, so there is no staged copy to keep consistent with it and
    a program's view cannot drift from what the call will commit.
    """

    paths: tuple[SearchPath, ...]
    """Every workspace root the read tools span, with their filters applied."""

    inner: OSAccess
    """The run's own filesystem, which answers for every unmounted path."""

    writable: tuple[SearchPath, ...] = ()
    """Roots whose ``.scratch/`` state this run may change.

    The writable span, narrower than the roots above: a program reads every
    workspace the user can see and parks state only in one they may mutate.
    Empty in a mode that may not write at all, which is what makes read and
    plan modes refuse a scratch write like every other.
    """

    max_document_chars: int = 5_000_000
    """Cap on one document, which is the only unbounded allocation a read makes.

    Per document rather than per run, because a decoded document is what the
    host actually holds: it is read whole and decoded in one go, then handed to
    the interpreter and dropped, so at most one is alive at a time however many
    a program opens.  Measured over a 2000-document, 100 MB workspace, reading
    every one of them moved the server's peak RSS by a megabyte.

    A running total would therefore bound nothing the host spends, while
    capping the very thing the mount exists for: a program that reads across
    the whole workspace so an answer need not quote from all of it.  What a
    program retains is the interpreter's own memory budget, and how long it
    spends retaining it is the request and tool timeouts, since the
    interpreter's duration budget does not count time spent in a host callback.
    """

    max_scratch_chars: int = 20_000_000
    written: int = 0
    """Cap on what one program may write to ``.scratch/``, and its running total.

    Cumulative where the read cap is not, because the resource is different:
    written characters land on disk and stay there, so a loop that writes the
    same megabyte a thousand times spends a gigabyte of it, which no other
    budget here bounds.
    """

    # -- routing --------------------------------------------------------

    @override
    def dispatch(
        self,
        function_name: OsFunction,
        args: tuple[Any, ...],
        kwargs: dict[str, Any] | None = None,
    ) -> Any:
        """Send one operation to the mount, or to the run's own filesystem.

        The one place the two filesystems are told apart, which is the seam
        ``AbstractOS`` offers for exactly this.  Asking per method instead cost
        a two-line prologue in each of eighteen of them and made forgetting one
        a silent bug in the *other* filesystem: an unimplemented ``path_open``
        refused ``/tmp`` as well as the workspace, since neither branch was
        ever reached.  Here a method this class does not implement is simply
        one the mount does not offer, and the run's own files keep answering.

        An operation naming no path at all -- the environment, the clock --
        belongs to the run, so it routes by the same rule with nothing to
        match.  A rename with one end mounted is the mount's, since the half
        that touches the workspace is what decides.
        """
        if any(_mounted(arg) for arg in args):
            return super().dispatch(function_name, args, kwargs)

        return self.inner.dispatch(function_name, args, kwargs)

    def _root(self, canonical: str) -> SearchPath | None:
        """The search path *canonical* names bare, if it names one at all.

        ``resolve_search_path`` drops a bare scope root deliberately, since no
        document tool takes one as an argument.  A mount does: ``/workspace/~``
        is the directory a program lists to find out what is there, so the case
        is answered here rather than by loosening the resolver every path tool
        shares.
        """
        match = match_scope(self.paths, canonical)

        return match[0] if match is not None and not match[1] else None

    def _entry(self, path: VirtualPath) -> Entry | None:
        """Resolve a mounted path and stat it, or ``None`` when it names nothing.

        A bare scope root resolves to the search path itself with an empty
        local name, which is how a listing of the mount's top level finds a
        workspace that has no entry of its own.  The mount root answers
        ``None``, since it is a directory of workspaces rather than an entry in
        any of them, and every caller has its own answer for that case.
        """
        canonical = _canonical(path)
        if canonical is None or canonical == ".":
            return None

        root = self._root(canonical)
        resolved = (
            (root, "", root.path)
            if root is not None
            else resolve_accessible_file(self.paths, canonical)
        )
        if resolved is None:
            return None

        sp, local, absolute = resolved
        if local and not entry_visible(sp, local, DEFAULT_EXCLUDE_DIRS):
            return None

        st = entry_stat(absolute)

        return None if st is None else Entry(sp, local, absolute, st)

    def _scratch_target(self, path: VirtualPath) -> Path:
        """The file a mounted write may touch, or a refusal naming what may.

        The three ways a write is turned away are three different situations
        and say so in turn: a run that may not write at all has nothing to
        offer but ``/tmp``, a path in a workspace the user may only read has
        nowhere to land, and a document is the user's to answer for, which a
        running program cannot stop to ask about.

        Resolved against the writable span rather than the mounted one, which
        is wider, and the scratch test is applied to the canonical local path
        rather than the spelling it was addressed by, so neither a ``..``
        segment nor a symlink can carry a ``.scratch`` part onto a document.
        """
        canonical = _canonical(path) or str(path)
        if not self.writable:
            raise PermissionError(
                f"'{canonical}' cannot be written in this chat mode. Park "
                "intermediates under /tmp, which is discarded when the call ends."
            )

        resolved = (
            None
            if canonical == "."
            else resolve_accessible_file(self.writable, canonical)
        )
        if resolved is None:
            raise PermissionError(
                f"'{canonical}' names no workspace this run may write to."
            )

        if not is_scratch_path(resolved[1]):
            raise PermissionError(
                f"'{canonical}' is one of the user's documents, and the mounted "
                "workspace is read-only. Name it as this call's `output_path` "
                "and write /output instead, or use the document write tools. A "
                "path under `.scratch/` can be written from here directly."
            )

        return resolved[2]

    # -- reads ----------------------------------------------------------

    @override
    def path_exists(self, path: PurePosixPath) -> bool:
        return _at_root(path) or self._entry(path) is not None

    @override
    def path_is_file(self, path: PurePosixPath) -> bool:
        entry = self._entry(path)

        return entry is not None and S_ISREG(entry.stat.st_mode)

    @override
    def path_is_dir(self, path: PurePosixPath) -> bool:
        if _at_root(path):
            return True

        entry = self._entry(path)

        return entry is not None and S_ISDIR(entry.stat.st_mode)

    @override
    def path_is_symlink(self, path: PurePosixPath) -> bool:
        # `entry_stat` reports a symlink as absent rather than following it, so
        # the mount never has one to admit to.
        return False

    @override
    def path_iterdir(self, path: PurePosixPath) -> list[PurePosixPath]:
        if _at_root(path):
            return [WORKSPACE_MOUNT / sp.prefixed("") for sp in self.paths]

        entry = self._entry(path)
        if entry is None:
            raise FileNotFoundError(f"[Errno 2] No such directory: {str(path)!r}")

        if not S_ISDIR(entry.stat.st_mode):
            raise NotADirectoryError(f"[Errno 20] Not a directory: {str(path)!r}")

        sp, local, absolute = entry.search_path, entry.local, entry.absolute

        # Sorted, because Monty cannot compare two `Path` values, so a program
        # that wants an order has no way to impose one on what it is handed.
        prefix = f"{local}/" if local else ""
        with os.scandir(absolute) as children:
            visible = [
                rel
                for child in children
                if entry_visible(
                    sp, (rel := f"{prefix}{child.name}"), DEFAULT_EXCLUDE_DIRS
                )
            ]

        return [WORKSPACE_MOUNT / sp.prefixed(rel) for rel in sorted(visible)]

    @override
    def path_stat(self, path: PurePosixPath) -> StatResult:
        if _at_root(path):
            return StatResult.dir_stat()

        entry = self._entry(path)
        if entry is None:
            raise FileNotFoundError(
                f"[Errno 2] No such file or directory: {str(path)!r}"
            )

        st = entry.stat
        if S_ISDIR(st.st_mode):
            return StatResult.dir_stat(mtime=st.st_mtime)

        return StatResult.file_stat(size=st.st_size, mtime=st.st_mtime)

    @override
    def path_open(self, path: PurePosixPath, mode: str) -> MontyFileHandle:
        """Answer ``open(path, mode)`` for the mount.

        A handle is a data holder naming the path, so all this owes is the
        open-time effect the mode asks for: a read proves the file is there
        before the program starts reading it, and a write goes through the
        same gate ``write_text`` does, which is what keeps ``open`` from being
        a second answer to which paths a program may change.
        """
        handle = MontyFileHandle(str(path), mode)
        if handle.binary:
            self._refuse_bytes(_named(path))

        if not handle.writable:
            if self.path_is_dir(path):
                raise IsADirectoryError(f"[Errno 21] Is a directory: {str(path)!r}")

            if not self.path_is_file(path):
                raise FileNotFoundError(
                    f"[Errno 2] No such file or directory: {str(path)!r}"
                )

            return handle

        absolute = self._scratch_target(path)
        absolute.parent.mkdir(parents=True, exist_ok=True)
        action = handle.mode[0]
        if action == "r" and not absolute.is_file():
            raise FileNotFoundError(
                f"[Errno 2] No such file or directory: {str(path)!r}"
            )

        if action == "w" or not absolute.exists():
            _ = absolute.write_text("", encoding="utf-8")

        return handle

    @override
    def path_read_text(self, path: PurePosixPath | MontyFileHandle) -> str:
        entry = self._entry(path)
        if entry is None:
            if _at_root(path):
                raise IsADirectoryError(f"[Errno 21] Is a directory: {_named(path)}")

            raise FileNotFoundError(
                f"[Errno 2] No such file or directory: {_named(path)}"
            )

        if not S_ISREG(entry.stat.st_mode):
            raise IsADirectoryError(f"[Errno 21] Is a directory: {_named(path)}")

        return self._decoded(
            entry.search_path.prefixed(entry.local), entry.absolute, entry.stat.st_size
        )

    @override
    def path_read_bytes(self, path: PurePosixPath | MontyFileHandle) -> bytes:
        self._refuse_bytes(_named(path))

    def _refuse_bytes(self, canonical: str) -> NoReturn:
        """Refuse a byte channel, whatever the document turns out to be.

        Text is the only thing a program can do anything with, and answering
        this before the file is even resolved keeps one answer to "what can
        this run open" rather than two.  A document with no text form at all
        is the read_binary_document tool's, which the model reaches from
        outside a program.
        """
        raise ValueError(
            f"'{canonical}' can only be read as text, since the sandbox "
            "serves no bytes from the workspace. Read it in text mode, or use "
            "the read_binary_document tool for a document with no text form."
        )

    def _decoded(self, canonical: str, absolute: Path, size: int) -> str:
        """Decode one document, bounded before and after.

        :func:`check_read_budget` bounds it by size, which is what keeps an
        oversized file out of memory in the first place, but a size is only an
        upper bound on a character count, so the exact length is checked once
        the text is in hand.
        """
        with translate_tool_retry(MemoryError):
            check_read_budget(canonical, size, self.max_document_chars)

        decoded = read_text_file(absolute)
        if decoded is None:
            # The exception types are the mount's own, since a `ToolRetry`
            # reaches the program as a bare `Exception`, but the sentence is
            # every reader's, sidecar included, so a program refused a binary
            # is sent to its extracted text rather than left guessing.
            raise ValueError(
                f"'{canonical}' {NOT_TEXT_REASON}.{sidecar_hint(canonical)}"
            )

        if len(decoded.text) > self.max_document_chars:
            raise MemoryError(
                f"'{canonical}' is too large to read here ({len(decoded.text)} "
                f"characters, and one document may hold at most "
                f"{self.max_document_chars})."
            )

        return decoded.text

    def _charge_write(self, canonical: str, data: str) -> None:
        """Hold one program's scratch writes to what a run may put on disk."""
        self.written += len(data)
        if self.written > self.max_scratch_chars:
            raise MemoryError(
                f"Writing '{canonical}' takes this program past the "
                f"{self.max_scratch_chars} characters one run may write to "
                "`.scratch/`."
            )

    # -- writes, which reach `.scratch/` and nothing else ----------------

    @override
    def path_write_text(self, path: PurePosixPath | MontyFileHandle, data: str) -> int:
        absolute = self._scratch_target(path)
        self._charge_write(_named(path), data)
        absolute.parent.mkdir(parents=True, exist_ok=True)
        _ = absolute.write_text(data, encoding="utf-8")

        return len(data)

    @override
    def path_write_bytes(
        self, path: PurePosixPath | MontyFileHandle, data: bytes
    ) -> int:
        _ = self._scratch_target(path)
        raise ValueError(
            f"'{_named(path)}' can only be written as text, since the "
            "workspace stores UTF-8."
        )

    @override
    def path_append_text(self, path: PurePosixPath | MontyFileHandle, data: str) -> int:
        absolute = self._scratch_target(path)
        self._charge_write(_named(path), data)
        absolute.parent.mkdir(parents=True, exist_ok=True)
        with absolute.open("a", encoding="utf-8") as handle:
            return handle.write(data)

    @override
    def path_append_bytes(
        self, path: PurePosixPath | MontyFileHandle, data: bytes
    ) -> int:
        return self.path_write_bytes(path, data)

    @override
    def path_mkdir(self, path: PurePosixPath, parents: bool, exist_ok: bool) -> None:
        # A scratch write creates whatever directories its path needs, so this
        # only has to agree about which ones a program may name.
        self._scratch_target(path).mkdir(parents=parents, exist_ok=exist_ok)

    @override
    def path_unlink(self, path: PurePosixPath) -> None:
        self._scratch_target(path).unlink()

    @override
    def path_rmdir(self, path: PurePosixPath) -> None:
        _ = self._scratch_target(path)
        raise PermissionError(
            f"'{_named(path)}' is a directory, which the sandbox does not "
            "remove. Remove the files it holds instead."
        )

    @override
    def path_rename(self, path: PurePosixPath, target: PurePosixPath) -> None:
        # Reached only when one end is mounted, and a rename with an end in the
        # workspace is a write of that end however it is spelled.
        raise PermissionError(
            f"'{_named(target)}' cannot be renamed into or out of the mounted "
            "workspace. Read the source and write the target instead."
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
