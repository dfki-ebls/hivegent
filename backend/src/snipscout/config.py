"""Configuration settings for the snipscout application."""

import re
from pathlib import Path, PurePosixPath
from typing import Literal

from pydantic import BaseModel
from pydantic_settings import BaseSettings, SettingsConfigDict

__all__ = [
    "DOCUMENT_EXTENSION",
    "EmbeddingSettings",
    "LlmSettings",
    "LogfireSettings",
    "McpSettings",
    "Settings",
    "sanitize_document_path",
    "sanitize_user_id",
    "settings",
]


# All documents are converted and stored as markdown.
DOCUMENT_EXTENSION = ".md"


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

    Configurable via ``SNIPSCOUT_LOGFIRE__ENABLED`` and
    ``SNIPSCOUT_LOGFIRE__TRACES_DIR``.
    """

    enabled: bool = True
    traces_dir: Path | None = None


class EmbeddingSettings(BaseModel):
    """Embedding model configuration for dense retrieval.

    Configurable via ``SNIPSCOUT_EMBEDDING__PROVIDER``,
    ``SNIPSCOUT_EMBEDDING__MODEL``, etc.
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


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_prefix="SNIPSCOUT_",
        env_nested_delimiter="__",
        env_file=".env",
        env_file_encoding="utf-8",
    )

    llm: LlmSettings = LlmSettings()
    embedding: EmbeddingSettings = EmbeddingSettings()
    logfire: LogfireSettings = LogfireSettings()
    mcp: McpSettings = McpSettings()

    data_dir: Path = Path("data")
    max_file_size_bytes: int = 10 * 1024 * 1024  # 10 MB
    cors_origins: list[str] = ["http://localhost:3000"]

    def get_traces_dir(self) -> Path:
        """Get the directory for trace output files.

        Returns:
            The configured traces directory, or ``data_dir / "traces"`` by
            default.
        """
        return self.logfire.traces_dir or self.data_dir / "traces"

    def get_user_dir(self, user_id: str) -> Path:
        """Get the root directory for a specific user.

        Args:
            user_id: The user ID.

        Returns:
            Path to the user's root directory.

        Raises:
            ValueError: If the user ID is invalid.
        """
        safe_id = sanitize_user_id(user_id)
        path = self.data_dir / safe_id
        path.mkdir(parents=True, exist_ok=True)
        return path

    def get_user_documents_dir(self, user_id: str) -> Path:
        """Get the documents directory for a specific user.

        Args:
            user_id: The user ID.

        Returns:
            Path to the user's documents directory.

        Raises:
            ValueError: If the user ID is invalid.
        """
        path = self.get_user_dir(user_id) / "documents"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def get_user_conversations_dir(self, user_id: str) -> Path:
        """Get the conversations directory for a specific user.

        Args:
            user_id: The user ID.

        Returns:
            Path to the user's conversations directory.

        Raises:
            ValueError: If the user ID is invalid.
        """
        path = self.get_user_dir(user_id) / "conversations"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def get_user_tokens_path(self, user_id: str) -> Path:
        """Get the tokens file path for a specific user.

        Args:
            user_id: The user ID.

        Returns:
            Path to the user's tokens file.

        Raises:
            ValueError: If the user ID is invalid.
        """
        return self.get_user_dir(user_id) / "tokens.json"

    def get_user_chunks_dir(self, user_id: str) -> Path:
        """Get the chunks directory for a specific user.

        Chunk JSON files are stored here after documents are chunked.

        Args:
            user_id: The user ID.

        Returns:
            Path to the user's chunks directory.

        Raises:
            ValueError: If the user ID is invalid.
        """
        path = self.get_user_dir(user_id) / "chunks"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def get_user_lancedb_dir(self, user_id: str) -> Path:
        """Get the LanceDB directory for a specific user.

        Args:
            user_id: The user ID.

        Returns:
            Path to the user's LanceDB directory.

        Raises:
            ValueError: If the user ID is invalid.
        """
        path = self.get_user_dir(user_id) / "lancedb"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def get_user_originals_dir(self, user_id: str) -> Path:
        """Get the originals directory for a specific user.

        Original binary files (PDF, DOCX, etc.) are stored here for potential
        re-conversion while their markdown versions are stored in documents/.

        Args:
            user_id: The user ID.

        Returns:
            Path to the user's originals directory.

        Raises:
            ValueError: If the user ID is invalid.
        """
        path = self.get_user_dir(user_id) / "originals"
        path.mkdir(parents=True, exist_ok=True)
        return path


settings = Settings()
