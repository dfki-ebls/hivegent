"""Configuration settings for the snipscout application."""

from enum import StrEnum
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

__all__ = ["FileExtension", "Settings", "settings"]


class FileExtension(StrEnum):
    """Allowed file extensions for document uploads."""

    TXT = ".txt"
    MD = ".md"


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_prefix="SNIPSCOUT_",
        env_file=".env",
        env_file_encoding="utf-8",
    )

    data_dir: Path = Path("data")
    conversations_dir: Path = Path("conversations")
    max_file_size_bytes: int = 10 * 1024 * 1024  # 10 MB


settings = Settings()
