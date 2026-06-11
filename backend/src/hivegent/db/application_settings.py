"""Repository for the :class:`ApplicationSettings` singleton row.

One pair of read/write helpers per toggle keeps callers decoupled from
the row layout: adding a future instance-wide switch means one column
on the model and one pair of functions here.
"""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert

from .engine import session
from .models import ApplicationSettings

__all__ = ["read_maintenance_enabled", "write_maintenance_enabled"]


async def read_maintenance_enabled() -> bool:
    """Return the persisted maintenance flag.

    An absent singleton row means all toggles are at their defaults,
    so it reads as ``False``.
    """
    async with session() as s:
        enabled = (
            await s.execute(sa.select(ApplicationSettings.maintenance_enabled))
        ).scalar_one_or_none()
    return bool(enabled)


async def write_maintenance_enabled(enabled: bool) -> None:
    """Persist the maintenance flag, materialising the singleton row if needed.

    Atomic ``INSERT ... ON CONFLICT DO UPDATE`` per the repository
    convention — concurrent toggles cannot race a read-then-write.
    """
    async with session() as s:
        stmt = pg_insert(ApplicationSettings).values(id=1, maintenance_enabled=enabled)
        await s.execute(
            stmt.on_conflict_do_update(
                index_elements=[ApplicationSettings.id],
                set_={"maintenance_enabled": stmt.excluded.maintenance_enabled},
            )
        )
