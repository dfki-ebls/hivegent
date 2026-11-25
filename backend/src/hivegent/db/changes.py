"""Whether the rows of a workspace change committed, asked of PostgreSQL by the id of their transaction.

A change's journal records the id inside the rows' transaction, before it
commits, so whoever finds the journal without its own commit record, a boot
or the change itself after a lost acknowledgement, asks `pg_xact_status`
whether the rows stand and keeps or rolls back the files to match.
"""

from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from .engine import session

__all__ = ["committed", "transaction_id"]


class _Xid8(sa.types.UserDefinedType[str]):
    """PostgreSQL's 64-bit transaction id, which `pg_xact_status` takes."""

    cache_ok = True

    def get_col_spec(self, **kw: Any) -> str:
        return "XID8"


async def transaction_id(s: AsyncSession) -> str:
    """The id of the caller's transaction, assigned now if it has none yet."""
    return str(
        await s.scalar(sa.select(sa.cast(sa.func.pg_current_xact_id(), sa.Text)))
    )


async def committed(xid: str) -> bool | None:
    """Whether the transaction *xid* committed, None when PostgreSQL no longer knows it."""
    async with session() as s:
        status = await s.scalar(
            sa.select(sa.func.pg_xact_status(sa.cast(xid, _Xid8())))
        )

    return None if status is None else status == "committed"
