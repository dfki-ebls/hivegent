"""Personal access token repository.

Tokens are stored Argon2id-hashed.  The raw token is only available at
creation time.  The token format is ``hivegent_<token_id>_<secret>``;
``<token_id>`` is the global PK of the :class:`~hivegent.db.Token` row.
"""

import asyncio
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from sqlalchemy import delete, select

from ..config import settings
from ..types import TokenInfo, User as UserModel
from ._common import affected_rows, ensure_user
from .engine import session
from .models import Token

__all__ = [
    "CreatedToken",
    "create_token",
    "list_tokens",
    "revoke_all_tokens",
    "revoke_token",
    "validate_token",
]


_TOKEN_PREFIX = "hivegent_"
_HASHER = PasswordHasher(time_cost=2, memory_cost=19456, parallelism=1)
# Verified on the miss path to flatten enumeration timing.
_DUMMY_HASH = _HASHER.hash("hivegent_dummy_for_timing_only")


@dataclass(slots=True, frozen=True)
class CreatedToken:
    """One-shot result of creating a personal access token."""

    raw: str
    info: TokenInfo


def _encode(token_id: str, secret: str) -> str:
    return f"{_TOKEN_PREFIX}{token_id}_{secret}"


def _decode_id(raw: str) -> str | None:
    """Extract the token id from a raw token, or return None for garbage."""
    if not raw.startswith(_TOKEN_PREFIX):
        return None
    rest = raw.removeprefix(_TOKEN_PREFIX)
    token_id, sep, _secret = rest.partition("_")
    return token_id if sep and token_id else None


def _to_info(t: Token) -> TokenInfo:
    return TokenInfo(
        id=t.id,
        name=t.name,
        created_at=t.created_at,
        expires_at=t.expires_at,
        last_used_at=t.last_used_at,
    )


async def create_token(
    user_id: str,
    name: str,
    expires_in_days: int | None = None,
) -> CreatedToken:
    """Create a personal access token for *user_id*.

    The :class:`User` row is materialized lazily — OIDC identities don't
    pre-create users elsewhere.
    """
    secret = secrets.token_urlsafe(32)
    expires_at = (
        datetime.now(UTC) + timedelta(days=expires_in_days)
        if expires_in_days is not None
        else None
    )
    async with session() as s:
        await ensure_user(s, user_id)
        token = Token(
            user_id=user_id,
            name=name,
            hash="",  # filled after we know the id
            expires_at=expires_at,
        )
        s.add(token)
        await s.flush()  # populate token.id and token.created_at
        raw = _encode(token.id, secret)
        token.hash = _HASHER.hash(raw)
        return CreatedToken(raw=raw, info=_to_info(token))


def _verify(hash: str, raw: str) -> bool:
    """Argon2 verify wrapped for ``asyncio.to_thread``."""
    try:
        _HASHER.verify(hash, raw)
    except VerifyMismatchError:
        return False
    return True


async def validate_token(raw: str) -> UserModel | None:
    """Validate a raw PAT and return the owning user, or None.

    Exactly one Argon2 verify runs per call (real on hit, dummy on miss)
    so timing does not leak which token ids exist.  The verify is
    CPU-bound and runs in a worker thread so the event loop stays free
    under concurrent requests.
    """
    token_id = _decode_id(raw)
    async with session() as s:
        token = await s.get(Token, token_id) if token_id is not None else None
        ok = await asyncio.to_thread(_verify, token.hash if token else _DUMMY_HASH, raw)
        if not ok or token is None:
            return None
        if token.expires_at is not None and datetime.now(UTC) > token.expires_at:
            return None

        now = datetime.now(UTC)
        throttle = settings.auth.last_used_throttle_seconds
        if (
            token.last_used_at is None
            or (now - token.last_used_at).total_seconds() >= throttle
        ):
            token.last_used_at = now
        return UserModel(id=token.user_id)


async def list_tokens(user_id: str) -> list[TokenInfo]:
    """Return all tokens owned by *user_id*, ordered by creation time."""
    async with session() as s:
        rows = (
            (
                await s.execute(
                    select(Token)
                    .where(Token.user_id == user_id)
                    .order_by(Token.created_at)
                )
            )
            .scalars()
            .all()
        )
    return [_to_info(t) for t in rows]


async def revoke_token(user_id: str, token_id: str) -> bool:
    """Revoke a token.  Returns True if a row was deleted."""
    async with session() as s:
        result = await s.execute(
            delete(Token).where(Token.id == token_id, Token.user_id == user_id)
        )
        return affected_rows(result) > 0


async def revoke_all_tokens(user_id: str) -> int:
    """Revoke every token owned by *user_id*.  Returns the deleted count."""
    async with session() as s:
        result = await s.execute(delete(Token).where(Token.user_id == user_id))
    return affected_rows(result)
