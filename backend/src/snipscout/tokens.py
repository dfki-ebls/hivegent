"""Personal Access Token storage and validation."""

import secrets
from datetime import datetime, timedelta, timezone

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from pydantic import BaseModel, TypeAdapter

from .config import settings
from .types import TokenInfo, User

__all__ = [
    "TokenStore",
    "token_store",
]


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

    def _load_user_tokens(self, user_id: str) -> list[_StoredToken]:
        """Load all tokens for a user."""
        path = settings.get_user_tokens_path(user_id)
        if not path.exists():
            return []
        return _StoredTokenListAdapter.validate_json(path.read_bytes())

    def _save_user_tokens(self, user_id: str, tokens: list[_StoredToken]) -> None:
        """Save all tokens for a user."""
        path = settings.get_user_tokens_path(user_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(_StoredTokenListAdapter.dump_json(tokens, indent=2))

    def create_token(
        self,
        user_id: str,
        name: str,
        expires_in_days: int | None = None,
    ) -> tuple[str, TokenInfo]:
        """Create a new personal access token.

        Args:
            user_id: The ID of the user creating the token.
            name: A user-provided name for the token.
            expires_in_days: Optional expiration in days from now.

        Returns:
            A tuple of (raw_token, token_info). The raw token is only
            available at creation time and cannot be retrieved later.
        """
        token_id = secrets.token_hex(8)
        token_secret = secrets.token_urlsafe(32)
        raw_token = f"snipscout_{user_id}_{token_id}_{token_secret}"

        token_hash = self._hasher.hash(raw_token)

        now = datetime.now(timezone.utc)
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

        token_info = TokenInfo(
            id=token_id,
            name=name,
            created_at=now,
            expires_at=expires_at,
        )

        return raw_token, token_info

    def validate_token(self, raw_token: str) -> User | None:
        """Validate a personal access token.

        Args:
            raw_token: The raw token string (snipscout_<user_id>_<token_id>_<secret>).

        Returns:
            A User instance if valid, None otherwise.
        """
        if not raw_token.startswith("snipscout_"):
            return None

        # Format: snipscout_{user_id}_{token_id}_{secret}
        parts = raw_token.split("_", 3)
        if len(parts) != 4:
            return None

        _, user_id, token_id, _ = parts

        tokens = self._load_user_tokens(user_id)
        for token in tokens:
            if token.id != token_id:
                continue

            try:
                self._hasher.verify(token.hash, raw_token)
            except VerifyMismatchError:
                continue

            # Check expiration
            if token.expires_at is not None:
                if datetime.now(timezone.utc) > token.expires_at:
                    return None

            # Update last_used_at
            token.last_used_at = datetime.now(timezone.utc)
            self._save_user_tokens(user_id, tokens)

            return User(id=token.user_id)

        return None

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
