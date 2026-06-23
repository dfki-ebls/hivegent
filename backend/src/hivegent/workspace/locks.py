"""Serialisation and in-flight conflict tracking for casebase mutations.

Every workspace mutation acquires the per-store async lock through
:func:`_locked_for`, which also rejects an op that would race a phased
upload or a bulk import still in flight.  The in-flight sets are consulted
by lock-free inventory reads to hide half-written entries.
"""

import asyncio
import threading
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager, contextmanager

from fastapi import HTTPException

from ..entries import stem_path_from_reference
from ..store import Casebase

__all__ = [
    "inflight_stems",
    "store_lock",
]


# Per-store async locks.  Created lazily; never removed because they are
# tiny and reusing the same Lock instance across the lifetime of a store
# is a feature.  ``threading.Lock`` guards the dict because asyncio.Lock
# instances bind to the event loop on first acquisition and the dict is
# also touched from synchronous teardown paths.
_locks: dict[str, asyncio.Lock] = {}
_locks_guard = threading.Lock()


def store_lock(store: Casebase) -> asyncio.Lock:
    """Return the asyncio lock guarding mutations on *store*."""
    key = store.store_key
    with _locks_guard:
        lock = _locks.get(key)
        if lock is None:
            lock = asyncio.Lock()
            _locks[key] = lock
    return lock


# Stems with an upload currently in flight, per store key.  Inventory reads
# walk the workspace without the casebase lock, so they consult this set to
# hide half-written entries — both during processing and during the rollback
# after a failed or cancelled upload — instead of surfacing them as ghost
# documents.
_inflight_stems: dict[str, set[str]] = {}

# Stores with a bulk import (a collection) in flight, by reference count.  A
# collection commits its files one at a time with the lock released in between,
# so a claim here blocks store-wide destructive ops (delete-all, directory
# delete/move) for its whole duration without blocking its own per-file uploads.
_inflight_store_claims: dict[str, int] = {}


def _add_inflight(store: Casebase, reference: str) -> None:
    """Mark *reference*'s stem as in flight (hidden from lock-free reads)."""
    _inflight_stems.setdefault(store.store_key, set()).add(
        stem_path_from_reference(reference)
    )


def _discard_inflight(store: Casebase, reference: str) -> None:
    """Clear an in-flight mark set by :func:`_add_inflight`."""
    stems = _inflight_stems.get(store.store_key)
    if stems is not None:
        stems.discard(stem_path_from_reference(reference))


@contextmanager
def _store_claim(store: Casebase) -> Iterator[None]:
    """Mark the whole store as having a bulk import in flight for the block.

    Re-entrant (reference counted) so two concurrent collections on one store
    each keep the claim alive until both finish.
    """
    key = store.store_key
    _inflight_store_claims[key] = _inflight_store_claims.get(key, 0) + 1
    try:
        yield
    finally:
        remaining = _inflight_store_claims.get(key, 0) - 1
        if remaining > 0:
            _inflight_store_claims[key] = remaining
        else:
            _inflight_store_claims.pop(key, None)


def inflight_stems(store: Casebase) -> frozenset[str]:
    """Stems with an upload in flight, to be hidden from lock-free reads."""
    return frozenset(_inflight_stems.get(store.store_key, ()))


def _reject_if_inflight(store: Casebase, reference: str) -> None:
    """Reject a mutation whose stem another phased upload already has in flight.

    A phased upload marks its stem the moment it claims the entry, so a second
    op on that stem 409s instead of racing the pending commit.  A markdown
    upload writes nothing to disk during reserve, so the in-flight set is the
    only thing that closes the window for it.
    """
    if stem_path_from_reference(reference) in inflight_stems(store):
        raise HTTPException(
            status_code=409, detail="Document is already being processed"
        )


def _reject_if_scope_inflight(store: Casebase, prefix: str | None) -> None:
    """Reject a directory- or store-wide mutation while work inside it runs.

    *prefix* is the directory whose contents the op removes or moves, or
    ``None`` for the whole store (delete-all).  A phased upload commits lock-free
    between its reserve and commit, and a bulk import commits its files one at a
    time; tearing down an enclosing directory in either window would strip files
    out from under a pending commit (orphaning an entry, or resurrecting one
    after a wipe).  The 409 defers the op until the in-flight work settles.
    """
    if _inflight_store_claims.get(store.store_key, 0) > 0:
        raise HTTPException(
            status_code=409, detail="A document in this scope is still being processed"
        )

    stems = inflight_stems(store)
    blocked = (
        stems if prefix is None else {s for s in stems if s.startswith(f"{prefix}/")}
    )
    if blocked:
        raise HTTPException(
            status_code=409, detail="A document in this scope is still being processed"
        )


@asynccontextmanager
async def _locked_for(
    store: Casebase,
    *entries: str,
    scope: str | None = None,
    whole_store: bool = False,
) -> AsyncIterator[None]:
    """Acquire the casebase lock for a mutation, rejecting in-flight conflicts.

    Routing every mutation's lock acquisition through here makes the in-flight
    check impossible to forget: pass the entry references a single-document op
    touches, ``scope`` for a directory subtree it removes or moves, or
    ``whole_store`` for a store-wide wipe.  A conflicting phased upload (or a
    bulk import claiming the store) is rejected with 409 so the op can never
    strip files out from under a pending commit.
    """
    async with store_lock(store):
        for entry in entries:
            _reject_if_inflight(store, entry)
        if whole_store:
            _reject_if_scope_inflight(store, None)
        elif scope is not None:
            _reject_if_scope_inflight(store, scope)

        yield
