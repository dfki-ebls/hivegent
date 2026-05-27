"""Per-user memory repository (replaces ``hivegent.memory``).

Memory is a single markdown blob per user.  The :class:`User` row is
materialized lazily so OIDC identities don't need pre-registration.
"""

from sqlalchemy import delete

from ._common import affected_rows
from .engine import session
from .models import Memory
from .users import ensure_user

__all__ = ["clear_memory", "load_memory", "save_memory"]


async def load_memory(user_id: str) -> str | None:
    """Return the user's memory content, or ``None`` if none is stored."""
    async with session() as s:
        mem = await s.get(Memory, user_id)
        return mem.content if mem is not None else None


async def save_memory(user_id: str, content: str) -> None:
    """Overwrite the user's memory blob."""
    async with session() as s:
        await ensure_user(s, user_id)
        mem = await s.get(Memory, user_id)
        if mem is None:
            s.add(Memory(user_id=user_id, content=content))
        else:
            mem.content = content


async def clear_memory(user_id: str) -> bool:
    """Delete the user's memory.  Returns ``True`` if a row was removed."""
    async with session() as s:
        result = await s.execute(delete(Memory).where(Memory.user_id == user_id))
        return affected_rows(result) > 0
