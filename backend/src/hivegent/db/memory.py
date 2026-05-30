"""Per-user memory repository (replaces ``hivegent.memory``).

Memory is a single markdown blob per user.  The :class:`User` row is
materialized lazily so OIDC identities don't need pre-registration.
"""

from sqlalchemy import delete, func
from sqlalchemy.dialects.postgresql import insert as pg_insert

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
    """Overwrite the user's memory blob.

    Concurrency-safe ``INSERT ... ON CONFLICT DO UPDATE`` so two requests
    saving memory for the same user cannot race the memory primary key.
    """
    async with session() as s:
        await ensure_user(s, user_id)
        await s.execute(
            pg_insert(Memory)
            .values(user_id=user_id, content=content)
            .on_conflict_do_update(
                index_elements=[Memory.user_id],
                set_={"content": content, "updated_at": func.now()},
            )
        )


async def clear_memory(user_id: str) -> bool:
    """Delete the user's memory.  Returns ``True`` if a row was removed."""
    async with session() as s:
        result = await s.execute(delete(Memory).where(Memory.user_id == user_id))
        return affected_rows(result) > 0
