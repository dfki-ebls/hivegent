"""Personal Access Token storage and validation."""

import json
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError

from .config import settings
from .types import User

__all__ = [
    "TokenInfo",
    "TokenStore",
    "token_store",
]


@dataclass
class TokenInfo:
    """Metadata about a personal access token."""

    id: str
    name: str
    created_at: datetime
    expires_at: datetime | None
    last_used_at: datetime | None


@dataclass
class _StoredToken:
    """Internal representation of a stored token."""

    id: str
    name: str
    hash: str
    user_id: str
    created_at: datetime
    expires_at: datetime | None
    last_used_at: datetime | None


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

        data = json.loads(path.read_text(encoding="utf-8"))
        tokens = []
        for item in data:
            tokens.append(
                _StoredToken(
                    id=item["id"],
                    name=item["name"],
                    hash=item["hash"],
                    user_id=item["user_id"],
                    created_at=datetime.fromisoformat(item["created_at"]),
                    expires_at=(
                        datetime.fromisoformat(item["expires_at"])
                        if item.get("expires_at")
                        else None
                    ),
                    last_used_at=(
                        datetime.fromisoformat(item["last_used_at"])
                        if item.get("last_used_at")
                        else None
                    ),
                )
            )
        return tokens

    def _save_user_tokens(self, user_id: str, tokens: list[_StoredToken]) -> None:
        """Save all tokens for a user."""
        path = settings.get_user_tokens_path(user_id)
        path.parent.mkdir(parents=True, exist_ok=True)

        data = []
        for token in tokens:
            data.append(
                {
                    "id": token.id,
                    "name": token.name,
                    "hash": token.hash,
                    "user_id": token.user_id,
                    "created_at": token.created_at.isoformat(),
                    "expires_at": (
                        token.expires_at.isoformat() if token.expires_at else None
                    ),
                    "last_used_at": (
                        token.last_used_at.isoformat() if token.last_used_at else None
                    ),
                }
            )

        path.write_text(json.dumps(data, indent=2), encoding="utf-8")

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
        # Generate token
        token_id = secrets.token_hex(8)
        token_secret = secrets.token_urlsafe(32)
        raw_token = f"snipscout_{token_id}_{token_secret}"

        # Hash the token
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
            last_used_at=None,
        )

        # Load existing tokens and add new one
        tokens = self._load_user_tokens(user_id)
        tokens.append(stored_token)
        self._save_user_tokens(user_id, tokens)

        token_info = TokenInfo(
            id=token_id,
            name=name,
            created_at=now,
            expires_at=expires_at,
            last_used_at=None,
        )

        return raw_token, token_info

    def validate_token(self, raw_token: str) -> User | None:
        """Validate a personal access token.

        Args:
            raw_token: The raw token string (snipscout_<id>_<secret>).

        Returns:
            A User instance if valid, None otherwise.
        """
        # Parse the token
        if not raw_token.startswith("snipscout_"):
            return None

        parts = raw_token.split("_", 2)
        if len(parts) != 3:
            return None

        token_id = parts[1]

        # Search all user token files
        if not settings.data_dir.exists():
            return None

        for user_dir in settings.data_dir.iterdir():
            if not user_dir.is_dir():
                continue

            token_file = user_dir / "tokens.json"
            if not token_file.exists():
                continue

            user_id = user_dir.name
            tokens = self._load_user_tokens(user_id)

            for token in tokens:
                if token.id != token_id:
                    continue

                # Verify the hash
                try:
                    self._hasher.verify(token.hash, raw_token)
                except VerifyMismatchError:
                    continue

                # Check expiration
                if token.expires_at is not None:
                    now = datetime.now(timezone.utc)
                    if now > token.expires_at:
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
