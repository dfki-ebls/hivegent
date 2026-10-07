"""Serialisation and in-flight conflict tracking for casebase mutations.

Every workspace mutation acquires the per-store async lock through
:func:`_locked_for`, which also rejects an op that would race a phased
upload or a bulk import still in flight.  The in-flight state is consulted
by lock-free inventory reads to hide half-written entries.
"""

import asyncio
import threading
from collections.abc import AsyncGenerator, Generator
from contextlib import AsyncExitStack, asynccontextmanager, contextmanager
from dataclasses import dataclass, field
from weakref import WeakValueDictionary

from fastapi import HTTPException

from ..entries import entry_owns, stem_path_from_reference
from ..l10n import Localized
from ..store import Casebase
from .operations import Root

__all__ = [
    "inflight_stems",
    "store_lock",
]


def _entry_inflight(path: str) -> Localized[str]:
    return Localized(
        en=f"Document is already being processed: {path}",
        de=f"Das Dokument wird bereits verarbeitet: {path}",
    )


def _scope_inflight(path: str) -> Localized[str]:
    return Localized(
        en=f"A document in {path} is still being processed",
        de=f"Ein Dokument in {path} wird noch verarbeitet",
    )


@dataclass(slots=True)
class _StoreState:
    """The in-flight state of one store, created on first use.

    Co-locating the two trackers keeps them addressed by a single registry
    entry, so they can never drift apart.
    """

    # Stems with an upload currently in flight.  Inventory reads walk the
    # workspace without the lock, so they consult this set to hide half-written
    # entries — both during processing and during the rollback after a failed or
    # cancelled upload — instead of surfacing them as ghost documents.  It also
    # rejects a second op on a stem a phased upload already holds.
    stems: set[str] = field(default_factory=set)

    # Store-wide claims held by bulk imports (collections) in flight, by
    # reference count.  A collection commits its files one at a time with the
    # lock released in between, so a claim here blocks store-wide destructive ops
    # (delete-all, directory delete/move) for its whole duration without blocking
    # its own per-file uploads.
    store_claims: int = 0


# Per-store in-flight state, created lazily and never removed (each entry is
# tiny and reusing it across a store's lifetime is a feature).  ``threading.Lock``
# guards the registry, since it may be reached from more than one loop or
# thread over the process lifetime.
_states: dict[str, _StoreState] = {}
_states_guard = threading.Lock()


def _state_for(store: Casebase) -> _StoreState:
    """Return *store*'s coordination state, creating it on first use."""
    key = store.store_key
    with _states_guard:
        state = _states.get(key)
        if state is None:
            state = _StoreState()
            _states[key] = state
    return state


# Every root's lock, by its `store_key`, held only while a change holds or
# awaits it, so a conversation's folder leaves no lock behind.  Only the event
# loop reaches it, and an unheld lock has no state worth keeping.
_locks: WeakValueDictionary[str, asyncio.Lock] = WeakValueDictionary()


def _lock_of(root: Root) -> asyncio.Lock:
    """The lock guarding changes to *root*, whichever kind it is."""
    lock = _locks.get(root.store_key)

    if lock is None:
        lock = _locks[root.store_key] = asyncio.Lock()

    return lock


def store_lock(store: Casebase) -> asyncio.Lock:
    """Return the asyncio lock guarding mutations on *store*."""
    return _lock_of(store)


def inflight_stems(store: Casebase) -> frozenset[str]:
    """Stems with an upload in flight, to be hidden from lock-free reads."""
    return frozenset(_state_for(store).stems)


def _add_inflight(store: Casebase, reference: str) -> None:
    """Mark *reference*'s stem as in flight (hidden from lock-free reads)."""
    _state_for(store).stems.add(stem_path_from_reference(reference))


def _discard_inflight(store: Casebase, reference: str) -> None:
    """Clear an in-flight mark set by :func:`_add_inflight`."""
    _state_for(store).stems.discard(stem_path_from_reference(reference))


@contextmanager
def _store_claim(store: Casebase) -> Generator[None]:
    """Mark the whole store as having a bulk import in flight for the block.

    Re-entrant (reference counted) so two concurrent collections on one store
    each keep the claim alive until both finish.
    """
    state = _state_for(store)
    state.store_claims += 1
    try:
        yield
    finally:
        state.store_claims -= 1


def _reject_if_inflight(store: Casebase, reference: str) -> None:
    """Reject a mutation whose stem another phased upload already has in flight.

    A phased upload marks its stem the moment it claims the entry, so a second
    op on that stem — or on one of the asset entries it owns, which are
    addressable by their own stems and would otherwise slip past an equality
    check — 409s instead of racing the pending commit and index.  A markdown
    upload writes nothing to disk during reserve, so the in-flight set is the
    only thing that closes the window for it.
    """
    stem = stem_path_from_reference(reference)
    if any(entry_owns(inflight, stem) for inflight in _state_for(store).stems):
        raise HTTPException(
            status_code=409,
            detail=_entry_inflight(store.scope.render(reference)).current,
        )


def _reject_if_scope_inflight(store: Casebase, prefix: str | None) -> None:
    """Reject a directory- or store-wide mutation while work inside it runs.

    *prefix* is the directory whose contents the op removes or moves, or
    ``None`` for the whole store (delete-all).  A phased upload prepares and
    indexes lock-free around its brief locked write, and a bulk import commits
    its files one at a time; tearing down an enclosing directory in any of those
    windows would strip files out from under the pending work (orphaning an
    entry, or resurrecting one after a wipe).  The 409 defers the op until the
    in-flight work settles.

    A prefix is blocked both when it *contains* an in-flight entry and when it
    *is* (or is inside) one's assets subtree, which the entry's own stem does
    not spell out.
    """
    state = _state_for(store)
    if state.store_claims > 0 or any(
        prefix is None or s.startswith(f"{prefix}/") or entry_owns(s, prefix)
        for s in state.stems
    ):
        raise HTTPException(
            status_code=409,
            detail=_scope_inflight(store.scope.render(prefix or "")).current,
        )


@asynccontextmanager
async def _locked(*roots: Root) -> AsyncGenerator[None]:
    """Hold the locks of every root in *roots* for the block.

    Taken in a stable ``store_key`` order, so two changes spanning the same
    roots in opposite directions (a move from the personal workspace into
    a group and one back) can never deadlock.
    """
    unique = {root.store_key: root for root in roots}

    async with AsyncExitStack() as stack:
        for key in sorted(unique):
            await stack.enter_async_context(_lock_of(unique[key]))

        yield


@asynccontextmanager
async def _locked_for(
    store: Casebase,
    *entries: str,
    scope: str | None = None,
    whole_store: bool = False,
) -> AsyncGenerator[None]:
    """Acquire the casebase lock for a mutation, rejecting in-flight conflicts.

    Routing every single-store mutation's lock acquisition through here makes
    the in-flight check impossible to forget: pass the entry references a
    single-document op touches, ``scope`` for a directory subtree it removes,
    or ``whole_store`` for a store-wide wipe.  A conflicting phased upload (or
    a bulk import claiming the store) is rejected with 409 so the op can never
    strip files out from under a pending commit.  A change spanning several
    casebases takes :func:`_locked` and checks each item itself.
    """
    async with _locked(store):
        for entry in entries:
            _reject_if_inflight(store, entry)
        if whole_store or scope is not None:
            _reject_if_scope_inflight(store, None if whole_store else scope)

        yield
