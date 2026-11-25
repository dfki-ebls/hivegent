"""Repository of the spent MCP confirmations, the `exar.mcp.Nonces` of the server."""

from datetime import UTC, datetime

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert

from .engine import session
from .models import McpConfirmation

__all__ = ["PostgresNonces"]


class PostgresNonces:
    """Spends each confirmation's nonce once in PostgreSQL, across restarts."""

    async def spend(self, nonce: str, expiry: int, /) -> bool:
        """Mark *nonce* spent until *expiry*, whether it was unspent, sweeping the expired ones.

        ``INSERT ... ON CONFLICT DO NOTHING`` makes the spend atomic, so of two
        concurrent retries with the same answer exactly one runs its call.
        """
        async with session() as s:
            await s.execute(
                sa.delete(McpConfirmation).where(
                    McpConfirmation.expires_at <= sa.func.now()
                )
            )
            spent = await s.execute(
                pg_insert(McpConfirmation)
                .values(nonce=nonce, expires_at=datetime.fromtimestamp(expiry, UTC))
                .on_conflict_do_nothing()
                .returning(McpConfirmation.nonce)
            )

            return spent.scalar_one_or_none() is not None
