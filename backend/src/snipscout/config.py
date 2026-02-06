"""Configuration settings for the snipscout application."""

import re
from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel
from pydantic_settings import BaseSettings, SettingsConfigDict

__all__ = [
    "BINARY_EXTENSIONS",
    "FileExtension",
    "LlmSettings",
    "McpSettings",
    "Settings",
    "TEXT_EXTENSIONS",
    "sanitize_user_id",
    "settings",
]


class FileExtension(StrEnum):
    """Allowed file extensions for document uploads."""

    # Text formats (stored as-is in documents/)
    TXT = ".txt"
    MD = ".md"
    HTML = ".html"
    XML = ".xml"
    CSV = ".csv"
    ADOC = ".adoc"

    # Binary formats (stored in originals/, converted to markdown)
    DOCX = ".docx"
    XLSX = ".xlsx"
    PPTX = ".pptx"
    PDF = ".pdf"
    PNG = ".png"
    JPG = ".jpg"
    JPEG = ".jpeg"


# Text-based extensions that can be stored directly without conversion
TEXT_EXTENSIONS = frozenset({
    FileExtension.TXT,
    FileExtension.MD,
    FileExtension.HTML,
    FileExtension.XML,
    FileExtension.CSV,
    FileExtension.ADOC,
})

# Binary extensions that require conversion to markdown
BINARY_EXTENSIONS = frozenset({
    FileExtension.DOCX,
    FileExtension.XLSX,
    FileExtension.PPTX,
    FileExtension.PDF,
    FileExtension.PNG,
    FileExtension.JPG,
    FileExtension.JPEG,
})


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


class LlmSettings(BaseModel):
    """LLM provider defaults, configurable via environment variables."""

    model: str = ""
    vision_model: str = ""
    small_model: str = ""
    api_key: str = ""
    base_url: str = ""


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
    mcp: McpSettings = McpSettings()

    data_dir: Path = Path("data")
    max_file_size_bytes: int = 10 * 1024 * 1024  # 10 MB
    cors_origins: list[str] = ["http://localhost:3000"]

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
