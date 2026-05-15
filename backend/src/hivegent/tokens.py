"""Personal Access Token storage and validation."""

import os
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from pydantic import BaseModel, TypeAdapter

from .config import sanitize_user_id, settings
from .store import Casebase
from .types import TokenInfo, User

__all__ = [
    "CreatedToken",
    "TokenStore",
    "token_store",
]


@dataclass(slots=True, frozen=True)
class CreatedToken:
    """Result of creating a personal access token.

    The raw token is only available at creation time and cannot be
    retrieved later.
    """

    raw_token: str
    info: TokenInfo


class _StoredToken(BaseModel):
    """Internal representation of a stored token."""

    id: str
    name: str
    hash: str
    user_id: str
    created_at: datetime
    expires_at: datetime | None = None
    last_used_at: datetime | None = None


_StoredTokenListAdapter = TypeAdapter(list[_StoredToken])


class TokenStore:
    """Storage and validation for personal access tokens.

    Tokens are stored per-user in JSON files with Argon2id-hashed values.
    The original token can only be retrieved at creation time.
    """

    def __init__(self) -> None:
        """Initialize the token store."""
        self._hasher = PasswordHasher(
            time_cost=2,
            memory_cost=19456,  # 19 MiB
            parallelism=1,
        )
        # Verified on every miss path to flatten enumeration timing.
        self._dummy_hash = self._hasher.hash("hivegent_dummy_for_timing_only")

    def _load_user_tokens(self, user_id: str) -> list[_StoredToken]:
        """Load all tokens for a user."""
        path = Casebase.for_user(user_id).tokens_path(settings.data_dir)
        if not path.exists():
            return []
        return _StoredTokenListAdapter.validate_json(path.read_bytes())

    def _save_user_tokens(self, user_id: str, tokens: list[_StoredToken]) -> None:
        """Save all tokens for a user using an atomic temp+rename."""
        path = Casebase.for_user(user_id).tokens_path(settings.data_dir)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = _StoredTokenListAdapter.dump_json(tokens, indent=2)
        tmp = path.with_suffix(path.suffix + f".tmp.{os.getpid()}")
        tmp.write_bytes(payload)
        tmp.replace(path)

    def create_token(
        self,
        user_id: str,
        name: str,
        expires_in_days: int | None = None,
    ) -> CreatedToken:
        """Create a new personal access token.

        Args:
            user_id: The ID of the user creating the token.
            name: A user-provided name for the token.
            expires_in_days: Optional expiration in days from now.

        Returns:
            CreatedToken with the raw token and metadata.
        """
        token_id = secrets.token_hex(8)
        token_secret = secrets.token_urlsafe(32)
        raw_token = f"hivegent_{user_id}_{token_id}_{token_secret}"

        token_hash = self._hasher.hash(raw_token)

        now = datetime.now(UTC)
        expires_at = None
        if expires_in_days is not None:
            expires_at = now + timedelta(days=expires_in_days)

        stored_token = _StoredToken(
            id=token_id,
            name=name,
            hash=token_hash,
            user_id=user_id,
            created_at=now,
            expires_at=expires_at,
        )

        tokens = self._load_user_tokens(user_id)
        tokens.append(stored_token)
        self._save_user_tokens(user_id, tokens)

        return CreatedToken(
            raw_token=raw_token,
            info=TokenInfo(
                id=token_id,
                name=name,
                created_at=now,
                expires_at=expires_at,
            ),
        )

    def validate_token(self, raw_token: str) -> User | None:
        """Validate a personal access token.

        Every well-formed token performs exactly one Argon2 verify (real
        for a token_id match, dummy otherwise) so an attacker can't use
        response timing to enumerate user IDs or distinguish
        "no such token" from "wrong secret".

        Args:
            raw_token: The raw token string (hivegent_<user_id>_<token_id>_<secret>).

        Returns:
            A User instance if valid, None otherwise.
        """
        if not raw_token.startswith("hivegent_"):
            return None

        # Format: hivegent_{user_id}_{token_id}_{secret}
        parts = raw_token.split("_", 3)
        if len(parts) != 4:
            return None

        _, user_id, token_id, _ = parts

        tokens: list[_StoredToken] = []
        try:
            sanitize_user_id(user_id)
            tokens = self._load_user_tokens(user_id)
        except ValueError:
            pass

        match = next((t for t in tokens if t.id == token_id), None)
        try:
            self._hasher.verify(match.hash if match else self._dummy_hash, raw_token)
        except VerifyMismatchError:
            return None

        if match is None:
            return None

        if match.expires_at is not None and datetime.now(UTC) > match.expires_at:
            return None

        self._maybe_touch_last_used(user_id, tokens, match)
        return User(id=match.user_id)

    def _maybe_touch_last_used(
        self,
        user_id: str,
        tokens: list[_StoredToken],
        token: _StoredToken,
    ) -> None:
        """Update ``last_used_at`` at most once per throttle window."""
        now = datetime.now(UTC)
        last = token.last_used_at
        throttle = settings.auth.last_used_throttle_seconds
        if last is not None and (now - last).total_seconds() < throttle:
            return
        updated = [
            t.model_copy(update={"last_used_at": now}) if t.id == token.id else t
            for t in tokens
        ]
        self._save_user_tokens(user_id, updated)

    def list_tokens(self, user_id: str) -> list[TokenInfo]:
        """List all tokens for a user.

        Args:
            user_id: The user ID to list tokens for.

        Returns:
            A list of TokenInfo instances (without the actual token values).
        """
        tokens = self._load_user_tokens(user_id)
        return [
            TokenInfo(
                id=token.id,
                name=token.name,
                created_at=token.created_at,
                expires_at=token.expires_at,
                last_used_at=token.last_used_at,
            )
            for token in tokens
        ]

    def revoke_token(self, user_id: str, token_id: str) -> bool:
        """Revoke a personal access token.

        Args:
            user_id: The user ID that owns the token.
            token_id: The ID of the token to revoke.

        Returns:
            True if the token was revoked, False if not found.
        """
        tokens = self._load_user_tokens(user_id)
        original_count = len(tokens)
        tokens = [t for t in tokens if t.id != token_id]

        if len(tokens) < original_count:
            self._save_user_tokens(user_id, tokens)
            return True

        return False


# Global token store instance
token_store = TokenStore()
