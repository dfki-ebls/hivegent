"""Configuration settings for the snipscout application."""

from pydantic_settings import BaseSettings, SettingsConfigDict

__all__ = ["Settings", "settings"]


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_prefix="SNIPSCOUT_",
        env_file=".env",
        env_file_encoding="utf-8",
    )


settings = Settings()
