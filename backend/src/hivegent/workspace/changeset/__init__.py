"""The one gateway every write, edit, move, delete, new directory, and upload commits through.

A :class:`~hivegent.tools.changes.Changeset` routed to
:class:`~hivegent.workspace.operations.Location`\\ s, a root and the path
local to it, is a batch of operations (:mod:`hivegent.tools.changes`):

* ``Write`` sets a text document's content (``mode`` replace, create,
  append, or prepend), creating it when it is missing.
* ``Edit`` applies a list of exact-string ``TextEdit`` in order and
  lands them as one write and one reindex.
* ``Move`` relocates an entry (its description, original, and assets) or
  a whole directory, within one workspace or across two.
* ``Delete`` removes an entry or a directory, refusing the other kind when
  it states which it ``expect``\\ s.
* ``CreateDir`` creates an empty directory.
* :class:`~hivegent.workspace.operations.Upload` lands a file's bytes as an
  entry, converted the way every upload is.  Only the host builds one, so it
  is never planned for an approval nor stored.  An ``overwrite`` replaces the
  entry at its stem, keeping its origin: in place when the upload consists of
  its files, and deleting it first when the entry has others, such as an
  original of another suffix.

An asset's description in an ``.assets`` payload, which only the host
changes, is a unit of its own: written and deleted without its asset, which
it needs, so a change leaving the description without its asset is refused.
Every unit is a subject with its companions (:class:`~hivegent.tools.entries.Part`),
an entry's description, original, and assets, and a case base's cbrkit
companions beside an entry's original or a directory, so a move or delete of the
subject takes every companion along.  The companions are never written,
uploaded, or moved on their own, the sidecar included while the format of
cbrkit's specifications still evolves, and a delete may take one alone, such
as a leftover sidecar.

A :class:`~hivegent.workspace.operations.Root` answers for its own rules, so
both kinds go through the one planner and executor below: one of a workspace,
whose entries carry rows, an index, and in-flight claims, and one of plain
files, such as a conversation's ``/tmp`` or a skill library.  A root without a store skips
what only an entry has: rows, a projection, in-flight checks, indexing, and
announcements.
Its file is a unit without an entry, a move to or from it is refused, and what
a changeset adds to it is put to its
:class:`~hivegent.workspace.operations.Quota`, which may name files the host
evicts.  Every root keeps what its policy reserves from ordinary changes.

The items apply at once, the way a diff of two states does: every source and
every basis names the workspace as it is now, and every destination and write
target the workspace once the changeset applied.  So chains (``a→b``,
``b→c``), swaps and rotations, a move onto a path another item vacates, moves
and deletes inside a directory another item moves, and a write to a file a
move carries all fit in one changeset, and the order of the items only decides
the order of their reports.

A basis is what a caller saw when it last read a file: the content hash the
read tools report, or the file's :class:`~hivegent.tools.entries.ContentStat`.  The
change is refused with a 409 when the file moved on since, and
``Write(mode="create")`` is the basis of a file that must not exist yet.

:func:`~hivegent.workspace.changeset.resolve.plan_changeset` plans a changeset and
:func:`~hivegent.workspace.changeset.apply.apply_changeset` commits it, see their
modules for the phases.
"""

from .apply import AppliedChangeset, IndexingFailed, RowChange, apply_changeset
from .gateway import Gateway
from .resolve import PlannedChangeset, plan_changeset

__all__ = [
    "AppliedChangeset",
    "Gateway",
    "IndexingFailed",
    "PlannedChangeset",
    "RowChange",
    "apply_changeset",
    "plan_changeset",
]
