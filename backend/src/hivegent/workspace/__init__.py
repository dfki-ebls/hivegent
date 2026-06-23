"""Single mutation gateway for casebase workspaces.

Every operation that modifies the workspace or the SQL documents for a
:class:`~hivegent.store.Casebase` goes through this package.  Each
public function acquires the per-store async lock so concurrent
mutations on the same casebase are serialised, then performs the
workspace and SQL writes in one step — see
:func:`hivegent.chunks.chunk_and_index_document` and
:func:`hivegent.chunks.delete_document`.

Chunks (text + vector) live next to documents in Postgres and cascade
on delete: any operation that drops a Document row also drops its
chunks in the same transaction.  Routes, agents, and MCP tools never
touch the workspace or the database directly — they call into this
package instead.

The filesystem is the source of truth for content; document rows and
chunks are an index derived from it.  Markdown changed or dropped on
disk by hand is folded back into SQL at startup by
:mod:`hivegent.reconcile`, and rows whose description file vanished are
dropped there — workspace files themselves are never deleted outside
the explicit mutation API.

The implementation is split into focused submodules:

* :mod:`~hivegent.workspace.locks` — per-store serialisation and in-flight
  conflict tracking.
* :mod:`~hivegent.workspace.paths` — pure path semantics and on-disk guards.
* :mod:`~hivegent.workspace.metadata` — entry-metadata derivation and the
  digest-skip reconciliation helper.
* :mod:`~hivegent.workspace.describe` — vision description envelope.
* :mod:`~hivegent.workspace.prepare` — lock-free per-kind upload preparation.
* :mod:`~hivegent.workspace.indexing` — markdown projection writes and the
  disk-to-SQL entry sync (owns the chunk/index primitives).
* :mod:`~hivegent.workspace.commit` — atomic commit, deletion, rollback, and
  the phased-upload lifecycle.
* :mod:`~hivegent.workspace.uploads`, :mod:`~hivegent.workspace.documents`,
  :mod:`~hivegent.workspace.assets`, :mod:`~hivegent.workspace.directories`,
  :mod:`~hivegent.workspace.collections` — the public mutation API.
"""

from .assets import (
    delete_asset_description,
    generate_asset_description,
    update_asset_description,
)
from .collections import process_collection
from .directories import (
    create_directory,
    delete_all,
    delete_directory,
    delete_workspace_root,
    move_directory,
    prune_empty_dirs,
)
from .documents import (
    delete_document,
    edit_document_text,
    move_document,
    rechunk,
    write_document_text,
)
from .indexing import sync_entries_from_disk, sync_entry_from_disk
from .locks import inflight_stems, store_lock
from .uploads import reconvert, replace_original, upload

__all__ = [
    "create_directory",
    "delete_all",
    "delete_asset_description",
    "delete_directory",
    "delete_document",
    "delete_workspace_root",
    "edit_document_text",
    "generate_asset_description",
    "inflight_stems",
    "move_directory",
    "move_document",
    "process_collection",
    "prune_empty_dirs",
    "rechunk",
    "reconvert",
    "replace_original",
    "store_lock",
    "sync_entries_from_disk",
    "sync_entry_from_disk",
    "update_asset_description",
    "upload",
    "write_document_text",
]
