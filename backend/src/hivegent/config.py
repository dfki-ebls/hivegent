"""Configuration settings for the hivegent application."""

import re
from pathlib import Path, PurePosixPath
from typing import Literal

from pydantic import BaseModel
from pydantic_settings import BaseSettings, SettingsConfigDict

from .converters.base import DOCUMENT_EXTENSION

__all__ = [
    "DOCUMENT_EXTENSION",
    "EmbeddingSettings",
    "GroupSettings",
    "LlmSettings",
    "LogfireSettings",
    "McpSettings",
    "Settings",
    "sanitize_document_path",
    "sanitize_group_id",
    "sanitize_user_id",
    "settings",
]

# Reserved top-level directory names in the data directory.
_RESERVED_NAMES = frozenset({"users", "groups", "traces"})


def sanitize_user_id(user_id: str) -> str:
    """Sanitize a user ID to prevent path traversal attacks.

    Args:
        user_id: The user ID to sanitize.

    Returns:
        The sanitized user ID.

    Raises:
        ValueError: If the user ID is invalid or contains unsafe characters.
    """
    if not user_id:
        raise ValueError("User ID cannot be empty")

    # Only allow alphanumeric characters, underscores, and hyphens
    sanitized = re.sub(r"[^a-zA-Z0-9_-]", "", user_id)

    if not sanitized or sanitized != user_id:
        raise ValueError(f"Invalid user ID: {user_id!r}")

    return sanitized


def sanitize_group_id(group_id: str) -> str:
    """Sanitize a group ID to prevent path traversal attacks.

    Args:
        group_id: The group ID to sanitize.

    Returns:
        The sanitized group ID.

    Raises:
        ValueError: If the group ID is invalid or contains unsafe characters.
    """
    if not group_id:
        raise ValueError("Group ID cannot be empty")

    sanitized = re.sub(r"[^a-zA-Z0-9_-]", "", group_id)

    if not sanitized or sanitized != group_id:
        raise ValueError(f"Invalid group ID: {group_id!r}")

    return sanitized


def sanitize_document_path(path: str) -> str:
    """Sanitize a document path to prevent path traversal attacks.

    Normalizes the path to POSIX forward slashes and rejects unsafe patterns.

    Args:
        path: The relative document path to sanitize.

    Returns:
        The sanitized POSIX-style relative path.

    Raises:
        ValueError: If the path is empty, absolute, or contains unsafe segments.
    """
    if not path:
        raise ValueError("Document path cannot be empty")

    if "\x00" in path:
        raise ValueError("Document path contains null bytes")

    # Normalize to POSIX forward slashes
    normalized = str(PurePosixPath(path.replace("\\", "/")))

    if normalized.startswith("/"):
        raise ValueError("Document path must be relative")

    # Reject . and .. segments
    for segment in normalized.split("/"):
        if segment in (".", ".."):
            raise ValueError(f"Document path contains unsafe segment: {segment!r}")
        if not segment:
            raise ValueError("Document path contains empty segment")

    return normalized


class LlmSettings(BaseModel):
    """LLM provider defaults, configurable via environment variables."""

    model: str = ""
    vision_model: str = ""
    small_model: str = ""
    api_key: str = ""
    base_url: str = ""


class LogfireSettings(BaseModel):
    """Logfire observability settings.

    Configurable via ``HIVEGENT_LOGFIRE__ENABLED`` and
    ``HIVEGENT_LOGFIRE__TRACES_DIR``.
    """

    enabled: bool = True
    traces_dir: Path | None = None


class EmbeddingSettings(BaseModel):
    """Embedding model configuration for dense retrieval.

    Configurable via ``HIVEGENT_EMBEDDING__PROVIDER``,
    ``HIVEGENT_EMBEDDING__MODEL``, etc.
    """

    provider: Literal["sentence-transformers", "openai"] = "sentence-transformers"
    model: str = "all-MiniLM-L6-v2"
    api_key: str = ""
    base_url: str = ""

    def fingerprint(self) -> dict[str, str]:
        """Return fields that define the vector space.

        Only includes provider and model — not api_key or base_url —
        because those don't affect the embedding dimensions or space.

        Returns:
            Dict with ``provider`` and ``model`` keys.
        """
        return {"provider": self.provider, "model": self.model}


class McpSettings(BaseModel):
    """MCP server settings for OIDC authentication."""

    client_id: str = ""
    client_secret: str = ""
    base_url: str = "http://localhost:8000/mcp"


class GroupSettings(BaseModel):
    """Settings for group-based knowledge sharing.

    Configurable via ``HIVEGENT_GROUPS__*`` environment variables.
    """

    groups_claim: str = "groups"
    default_permission: str = "read"


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_prefix="HIVEGENT_",
        env_nested_delimiter="__",
        env_file=".env",
        env_file_encoding="utf-8",
    )

    llm: LlmSettings = LlmSettings()
    embedding: EmbeddingSettings = EmbeddingSettings()
    logfire: LogfireSettings = LogfireSettings()
    mcp: McpSettings = McpSettings()
    groups: GroupSettings = GroupSettings()

    data_dir: Path = Path("data")
    max_file_size_bytes: int = 50 * 1024 * 1024  # 50 MB
    max_collection_size_bytes: int = 2 * 1024 * 1024 * 1024  # 2 GB
    max_collection_files: int = 10_000
    cors_origins: list[str] = ["http://localhost:3000"]

    def get_traces_dir(self) -> Path:
        """Get the directory for trace output files.

        Returns:
            The configured traces directory, or ``data_dir / "traces"`` by
            default.
        """
        return self.logfire.traces_dir or self.data_dir / "traces"


settings = Settings()
