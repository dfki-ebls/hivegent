"""Serialisation of workspace mutations.

Every mutation holds the lock of each root it changes for its whole commit,
its indexing included, so no other mutation sees an entry without its chunks.
"""

import asyncio
from collections.abc import AsyncGenerator
from contextlib import AsyncExitStack, asynccontextmanager
from weakref import WeakValueDictionary

from .operations import Root

__all__ = ["root_lock"]


# Every root's lock, by its `store_key`, held only while a change holds or
# awaits it, so a conversation's folder leaves no lock behind.  Only the event
# loop reaches it, and an unheld lock has no state worth keeping.
_locks: WeakValueDictionary[str, asyncio.Lock] = WeakValueDictionary()


def root_lock(root: Root) -> asyncio.Lock:
    """The lock guarding changes to *root*, whichever kind it is.

    Every mutation takes it, so this is where a workspace granted short of
    writing is refused, whichever surface the change came from, a source's
    mirror always among them for a principal.

    Raises:
        AccessDenied: When *root* is a workspace not granted for writing.
    """
    if root.store is not None:
        root.store.require_writable()

    lock = _locks.get(root.store_key)

    if lock is None:
        lock = _locks[root.store_key] = asyncio.Lock()

    return lock


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
            await stack.enter_async_context(root_lock(unique[key]))

        yield
