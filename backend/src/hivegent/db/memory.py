"""Per-user memory repository (replaces ``hivegent.memory``).

Memory is a single markdown blob per user.  The :class:`User` row is
materialized lazily so OIDC identities don't need pre-registration.
"""

from collections.abc import Callable

from sqlalchemy import ColumnElement, delete, func, select, update

from ..store import User
from ._common import affected_rows, ensure_row
from .engine import session
from .models import Memory
from .users import ensure_user

__all__ = ["clear_memory", "load_memory", "update_memory"]


def _owned(user: User) -> ColumnElement[bool]:
    """Match the memory of *user*, the one row every statement here names."""
    return Memory.user_id == user.id


async def load_memory(user: User) -> str | None:
    """Return the user's memory content, or ``None`` if none is stored."""
    async with session() as s:
        return await s.scalar(select(Memory.content).where(_owned(user)))


async def update_memory(user: User, change: Callable[[str], str]) -> str:
    """Apply *change* to the user's memory and return what it stored.

    The row is created empty if missing and locked for the read-modify-write,
    so two conversations editing at once apply their changes one after the
    other instead of one overwriting the other.  An exception raised by
    *change* rolls the transaction back and propagates.
    """
    async with session() as s:
        await ensure_user(s, user.id)
        await ensure_row(s, Memory, user_id=user.id, content="")
        current = await s.scalar(
            select(Memory.content).where(_owned(user)).with_for_update()
        )
        content = change(current or "")
        await s.execute(
            update(Memory)
            .where(_owned(user))
            .values(content=content, updated_at=func.now())
        )

        return content


async def clear_memory(user: User) -> bool:
    """Delete the user's memory.  Returns ``True`` if a row was removed."""
    async with session() as s:
        result = await s.execute(delete(Memory).where(_owned(user)))
        return affected_rows(result) > 0
