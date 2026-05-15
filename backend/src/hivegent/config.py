"""Configuration settings for the hivegent application."""

import re
from pathlib import Path, PurePosixPath
from typing import Literal

from pydantic import BaseModel
from pydantic_settings import BaseSettings, SettingsConfigDict

from .converters.base import DOCUMENT_EXTENSION

__all__ = [
    "DOCUMENT_EXTENSION",
    "AuthSettings",
    "EmbeddingSettings",
    "GroupSettings",
    "LimitsSettings",
    "LlmSettings",
    "LogfireSettings",
    "McpSettings",
    "NetworkSettings",
    "SecuritySettings",
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
    """LLM provider defaults, configurable via environment variables.

    ``model`` drives the main chat agent and any subagents (e.g.
    exploration), since those run with large contexts and tool calling
    where tiny models tend to fail.  ``aux_model`` powers supplementary
    one-shot workloads — document conversion, alt-text generation,
    title generation, compaction, and LLM-guided chunking — so it must
    be small, fast, and vision-capable.  Those workloads call it many
    times per document (especially alt-text and chunking), so cost and
    latency dominate over raw reasoning quality.  Both share the same
    ``api_key`` and ``base_url``.
    """

    model: str = ""
    aux_model: str | None = None
    api_key: str = ""
    base_url: str = ""


class LogfireSettings(BaseModel):
    """Logfire observability settings.

    Configurable via ``HIVEGENT_LOGFIRE__ENABLE``,
    ``HIVEGENT_LOGFIRE__TRACES_DIR``, and
    ``HIVEGENT_LOGFIRE__RETENTION_DAYS``.
    """

    enable: bool = True
    traces_dir: Path | None = None
    # Delete daily ``YYYY-MM-DD.jsonl`` files older than this many days at
    # startup.  Set to 0 to keep files indefinitely.
    retention_days: int = 7


class EmbeddingSettings(BaseModel):
    """Embedding model configuration for dense retrieval.

    Configurable via ``HIVEGENT_EMBEDDING__PROVIDER``,
    ``HIVEGENT_EMBEDDING__MODEL``, etc.
    """

    provider: Literal["sentence-transformers", "openai"] = "sentence-transformers"
    model: str = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
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
    """MCP server settings for OIDC authentication.

    ``enable`` controls whether the ``/mcp`` HTTP endpoint is mounted.
    ``mode`` selects ``proxy`` (FastMCP proxies the OAuth flow with a
    pre-registered ``client_id``/``client_secret``) or ``remote`` (clients
    register themselves with the upstream via RFC 7591 dynamic client
    registration, e.g. Rauthy; FastMCP only verifies JWTs).
    """

    enable: bool = True
    mode: Literal["proxy", "remote"] = "proxy"
    client_id: str = ""
    client_secret: str = ""
    base_url: str = "http://localhost:8000/mcp"


class GroupSettings(BaseModel):
    """Settings for group-based knowledge sharing.

    Configurable via ``HIVEGENT_GROUPS__*`` environment variables.
    """

    groups_claim: str = "groups"
    default_permission: str = "read"


class AuthSettings(BaseModel):
    """OIDC authentication and personal access token settings.

    Configurable via ``HIVEGENT_AUTH__*`` environment variables.

    ``jwks_timeout_seconds`` caps both the OIDC discovery and JWKS
    fetches; ``last_used_throttle_seconds`` debounces ``last_used_at``
    writes for personal access tokens so an authenticated burst doesn't
    rewrite the per-user token JSON on every request.
    """

    enable: bool = True
    issuer: str = ""
    audience: str | None = None
    jwks_cache_ttl: int = 3600
    jwks_timeout_seconds: float = 10.0
    last_used_throttle_seconds: int = 60


class SecuritySettings(BaseModel):
    """SSRF, CORS, and transport-safety settings.

    ``allow_private_urls`` opens the SSRF filter so user-supplied URLs
    (LLM ``base_url``, MCP server URLs, ``WebFetch``) may dial private
    or loopback addresses. Default off; turn on only for trusted
    self-hosted deployments where every authenticated user is already
    allowed to reach the same network.

    ``cors_origins`` is an explicit allow-list; ``"*"`` is rejected at
    startup because it silently disables credentialed CORS.
    """

    allow_private_urls: bool = False
    cors_origins: list[str] = ["http://localhost:3000"]
    cors_allow_credentials: bool = True
    cors_allow_methods: list[str] = [
        "GET",
        "POST",
        "PUT",
        "PATCH",
        "DELETE",
        "OPTIONS",
    ]
    cors_allow_headers: list[str] = ["Authorization", "Content-Type", "Accept"]


class LimitsSettings(BaseModel):
    """Upload, collection, and decoder size/count limits.

    ``upload_read_chunk_size`` controls the streaming read buffer used
    when ingesting collection ZIPs; larger values trade memory for
    fewer ``await`` hops.

    ``max_image_pixels`` raises Pillow's decompression-bomb threshold
    so large embedded images inside PDFs (common with scanned pages)
    decode successfully; the value still guards against truly
    degenerate inputs.
    """

    max_file_size_bytes: int = 50 * 1024 * 1024  # 50 MB
    max_collection_size_bytes: int = 512 * 1024 * 1024  # 512 MB
    max_collection_files: int = 10_000
    upload_read_chunk_size: int = 1024 * 1024  # 1 MB
    max_image_pixels: int = 1_000_000_000  # ~3 GB uncompressed


class NetworkSettings(BaseModel):
    """Outbound HTTP client and WebFetch tunables.

    ``connect_timeout_seconds`` applies to every outbound request made
    through the shared HTTP client (LLM, embeddings, MCP, JWKS).  The
    ``webfetch_*`` knobs only apply to the ``WebFetch`` agent tool.
    """

    connect_timeout_seconds: float = 5.0
    webfetch_timeout_seconds: float = 10.0
    webfetch_max_response_bytes: int = 1_000_000
    webfetch_max_redirects: int = 5


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
    auth: AuthSettings = AuthSettings()
    security: SecuritySettings = SecuritySettings()
    limits: LimitsSettings = LimitsSettings()
    network: NetworkSettings = NetworkSettings()

    data_dir: Path = Path("data")
    # Background tick that retries any documents whose inline index
    # write failed (``indexed_at = None``).  Set to 0 to disable.
    consistency_tick_interval_seconds: int = 600

    def get_traces_dir(self) -> Path:
        """Get the directory for trace output files.

        Returns:
            The configured traces directory, or ``data_dir / "traces"`` by
            default.
        """
        return self.logfire.traces_dir or self.data_dir / "traces"


settings = Settings()
