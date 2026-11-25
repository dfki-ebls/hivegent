"""Per-user memory, one markdown page per person, as an `exar.cbrkit.StorageMemory`.

The page is the ``content`` of the person's row in ``memory``, read and
written by a cbrkit storage scoped to that row.  The :class:`User` row is
materialized lazily before a write so OIDC identities don't need
pre-registration.
"""

from collections.abc import Mapping
from dataclasses import dataclass

import sqlalchemy as sa
from cbrkit.indexable import SQLAlchemy
from cbrkit.retrieve import Retriever
from cbrkit.sim.strings import WordCoverage
from exar.cbrkit import StorageMemory
from exar.typing import Changes

from ..store import User
from .engine import get_engine, session
from .models import Memory
from .users import ensure_user

__all__ = ["clear_memory", "user_memory"]

_PAGES = Memory.metadata.tables[Memory.__tablename__]


def _page(user: User) -> SQLAlchemy[str, str]:
    """The storage of *user*'s page, keyed by the user id.

    Raises:
        AccessDenied: For a service principal, which answers many people and
            so remembers nobody.
    """
    user.require_person()

    return SQLAlchemy[str, str](
        get_engine(),
        table=_PAGES,
        value_column=Memory.content.key,
        scope=Memory.user_id == user.id,
    )


@dataclass(frozen=True, slots=True, kw_only=True)
class _Memory(StorageMemory[str, str]):
    user_id: str

    async def write(
        self,
        changes: Changes[str, str],
        /,
        *,
        expected: Mapping[str, str] | None = None,
    ) -> Changes[str, str]:
        async with session() as s:
            await ensure_user(s, self.user_id)

        return await StorageMemory.write(self, changes, expected=expected)


def user_memory(user: User) -> StorageMemory[str, str]:
    """The memory of a person, one page under the person's user id, searched by the words it shares with a query.

    Raises:
        AccessDenied: For a service principal.
    """
    return _Memory(
        storage=_page(user),
        retriever=lambda limit: [
            Retriever(measure=WordCoverage(case_sensitive=False), limit=limit)
        ],
        conversion=str,
        user_id=user.id,
    )


async def clear_memory(user: User) -> bool:
    """Delete the user's memory.  Returns ``True`` if a row was removed."""
    return bool(await _page(user).delete_where(sa.true()))
