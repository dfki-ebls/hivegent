"""Configuration settings for the hivegent application."""

import hashlib
import os
import re
from pathlib import Path, PurePosixPath
from typing import Literal

from pydantic import BaseModel
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
    TomlConfigSettingsSource,
)

from .converters.base import DOCUMENT_EXTENSION

CONFIG_FILE_ENV_VAR = "HIVEGENT_CONFIG_FILE"
DEFAULT_CONFIG_FILE = Path("config.toml")

#: The fixed role name that grants administrator privileges.  Not
#: configurable: admin is a global capability, fully independent of the
#: groups used to share knowledge.
ADMIN_ROLE = "admin"

__all__ = [
    "ADMIN_ROLE",
    "DOCUMENT_EXTENSION",
    "AuthSettings",
    "ClaimSettings",
    "DatabaseSettings",
    "EmbeddingSettings",
    "LimitsSettings",
    "LlmSettings",
    "LogfireSettings",
    "McpSettings",
    "NetworkSettings",
    "RerankSettings",
    "SecuritySettings",
    "Settings",
    "content_digest",
    "content_hash",
    "sanitize_document_path",
    "sanitize_group_id",
    "sanitize_user_id",
    "settings",
]


def content_digest(text: str) -> str:
    """Return the full SHA-256 hex digest of *text*.

    Database operations store this as a drift fingerprint, so the reconciler
    can skip re-indexing an entry whose on-disk bytes are unchanged.

    >>> content_digest("hello world")
    'b94d27b9934d3e08a52e52d7da7dabfac484efe37a5380ee9088f7ace2efcde9'
    """
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def content_hash(text: str) -> str:
    """Return a short content fingerprint for optimistic-concurrency checks.

    The read tools surface this hash so a later edit or write can pass it
    back as ``expected_hash``; the mutation gateway then rejects the change
    if the document moved on in between, guarding against a model acting on
    a stale read.

    Args:
        text: The full document text to fingerprint.

    Returns:
        The first 12 hex characters of the SHA-256 digest of *text*.

    >>> content_hash("hello world")
    'b94d27b9934d'
    """
    return content_digest(text)[:12]


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
    """OpenTelemetry tracing settings.

    Tracing is enabled when either ``otlp_endpoint`` is set (sends to a
    self-hosted OTLP/HTTP backend such as Grafana Tempo) or the
    ``LOGFIRE_TOKEN`` environment variable is set (sends to Pydantic
    Logfire SaaS — convenient for local development).  When neither is
    configured, no exporter is installed and instrumentation is skipped
    entirely.

    Configurable via ``HIVEGENT_LOGFIRE__*`` environment variables.
    """

    otlp_endpoint: str | None = None
    service_name: str = "hivegent"


class EmbeddingSettings(BaseModel):
    """Embedding model configuration for dense retrieval.

    Configurable via ``HIVEGENT_EMBEDDING__PROVIDER``,
    ``HIVEGENT_EMBEDDING__MODEL``, etc.

    ``dimension`` is the vector size the ``chunks.embedding`` column and
    every Alembic migration are built against.  It defaults to ``384``,
    the output size of the default ``sentence-transformers`` model, and
    must be set explicitly (``HIVEGENT_EMBEDDING__DIMENSION``) whenever
    ``model`` is changed to one with a different size (e.g. ``1536`` for
    ``text-embedding-3-small``).  Keeping it a plain configured value —
    rather than probing the model — means importing the ORM never loads
    embedding weights; the boot-time guard validates the live column
    against this number.
    """

    provider: Literal["sentence-transformers", "openai"] = "sentence-transformers"
    model: str = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
    dimension: int = 384
    api_key: str = ""
    base_url: str = ""
    text_search_config: str | list[str] = ["german", "english"]
    """PostgreSQL FTS configuration(s) for the sparse (keyword) channel.

    Drives both the ``chunks.tsv`` generated column and the query-side
    ``plainto_tsquery`` (passed to cbrkit as ``tsvector_config``), kept
    in lockstep through this single value.  A list stems the corpus with
    every listed dictionary at once — ``["german", "english"]`` indexes
    the union ``to_tsvector('german', text) || to_tsvector('english',
    text)`` and queries it the same way, so German morphology (compounds,
    inflection) and English are both handled properly for the
    Germany-based, bilingual audience.  Use the single language-agnostic
    ``"simple"`` (no stemming) instead when the language set is unknown.
    Changing it requires a follow-up Alembic revision rewriting the
    generated column.
    """

    def fingerprint(self) -> dict[str, str]:
        """Return fields that define the vector space.

        Only includes provider and model — not api_key or base_url —
        because those don't affect the embedding dimensions or space.

        Returns:
            Dict with ``provider`` and ``model`` keys.
        """
        return {"provider": self.provider, "model": self.model}


class RerankSettings(BaseModel):
    """Cross-encoder re-ranking applied after hybrid retrieval.

    Disabled unless both ``provider`` and ``model`` are set (mirrors how
    ``aux_model`` gates image captioning).  When enabled, the base retriever
    over-fetches ``candidate_multiplier`` times the requested results and the
    reranker rescores that candidate pool, letting separately-stored caption
    chunks compete on relevance rather than raw fusion score.

    ``provider`` selects the cbrkit reranker backend:

    - ``sentence-transformers``: a local cross-encoder, no external calls
      (e.g. ``BAAI/bge-reranker-v2-m3`` for the bilingual German/English corpus).
    - ``http``: an HTTP ``/rerank`` endpoint, e.g. vLLM, resolved from
      ``base_url``.  Set ``api_key`` to authenticate it with a bearer token.

    Disabled by default to keep retrieval latency low; enabling trades
    runtime for ranking quality.

    Configurable via ``HIVEGENT_RERANK__PROVIDER``, ``HIVEGENT_RERANK__MODEL``,
    etc.
    """

    provider: Literal["sentence-transformers", "http"] | None = None
    model: str = ""
    api_key: str = ""
    base_url: str = ""
    candidate_multiplier: int = 5
    top_n: int | None = None

    @property
    def enabled(self) -> bool:
        """Whether a reranker is configured (provider and model both set)."""
        return self.provider is not None and bool(self.model)


class McpSettings(BaseModel):
    """MCP server settings for OIDC authentication.

    ``enable`` controls whether the ``/mcp`` HTTP endpoint is mounted.
    ``mode`` selects ``proxy`` (FastMCP proxies the OAuth flow with a
    pre-registered ``client_id``/``client_secret``) or ``remote`` (clients
    register themselves with the upstream via RFC 7591 dynamic client
    registration, e.g. Rauthy; FastMCP only verifies JWTs).
    """

    enable: bool = False
    mode: Literal["proxy", "remote"] = "proxy"
    client_id: str = ""
    client_secret: str = ""
    base_url: str = "http://localhost:8000/mcp"
    allow_unauthenticated: bool = False


class ClaimSettings(BaseModel):
    """OIDC claim names and how their group entries are interpreted.

    Configurable via ``HIVEGENT_CLAIMS__*`` environment variables.

    ``groups`` is the claim carrying shared-knowledge group memberships
    (entries like ``"engineering:write"``); ``roles`` is the claim
    carrying global roles (e.g. the fixed :data:`ADMIN_ROLE`).
    ``default_group_permission`` is the permission granted to a bare
    group entry — one without a ``:read``/``:write`` suffix.
    """

    groups: str = "groups"
    roles: str = "roles"
    default_group_permission: Literal["read", "write"] = "write"


class AuthSettings(BaseModel):
    """OIDC authentication settings.

    Configurable via ``HIVEGENT_AUTH__*`` environment variables.

    ``audience`` is the set of accepted token audiences.  The IdP stamps
    each token's ``aud`` with the issuing client's id, so this confines
    the API to its own clients.  An entry ending in ``*`` matches by
    prefix, so a single ``"hivegent-*"`` accepts every current and future
    ``hivegent-`` client without a config change; other entries match
    exactly.  A token whose ``aud`` matches nothing is rejected.  Required
    when ``enable`` is true.  ``jwks_timeout_seconds`` caps both the OIDC
    discovery and JWKS fetches.
    """

    enable: bool = True
    allow_disabled: bool = False
    issuer: str = ""
    audience: list[str] = []
    jwks_cache_ttl: int = 3600
    jwks_force_refresh_min_interval_seconds: int = 60
    jwks_timeout_seconds: float = 10.0


class SecuritySettings(BaseModel):
    """SSRF and transport-safety settings.

    ``allow_private_urls`` opens the SSRF filter so user-supplied URLs
    (LLM ``base_url``, MCP server URLs, ``WebFetch``) may dial private
    or loopback addresses. Default off, turn on only when authenticated
    users are allowed to reach the same network. Server-configured URLs
    are trusted operator input and do not need this setting.

    CORS, security headers, rate limiting, and body-size caps are
    enforced by the Caddy reverse proxy, not here.
    """

    allow_private_urls: bool = False
    expose_api_docs: bool = False


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


class DatabaseSettings(BaseModel):
    """Database backend configuration.

    ``url`` is a mandatory async SQLAlchemy URL (e.g.
    ``postgresql+psycopg://...``).  The only supported dialect is
    PostgreSQL with the ``pgvector`` extension; there is no fallback.
    """

    url: str = ""
    echo: bool = False


class NetworkSettings(BaseModel):
    """Outbound HTTP client and WebFetch tunables.

    ``connect_timeout_seconds`` applies to every outbound request made
    through the shared HTTP client (LLM, embeddings, MCP, JWKS).  The
    ``webfetch_*`` knobs only apply to the ``WebFetch`` agent tool.
    ``llm_request_timeout_seconds`` caps individual non-streaming LLM
    calls (image description, document conversion, title generation,
    compaction, sub-agent / retrieval tool runs) so a hung inference
    server cannot stall a handler indefinitely.  The default leaves
    enough headroom for long PDFs and long compaction prompts; bump it
    further if your provider is slow.  Streaming chat is governed by
    client disconnect, not by this timeout.
    """

    connect_timeout_seconds: float = 5.0
    webfetch_timeout_seconds: float = 10.0
    webfetch_max_response_bytes: int = 1_000_000
    webfetch_max_redirects: int = 5
    llm_request_timeout_seconds: float = 600.0


class Settings(BaseSettings):
    """Application settings resolved from init kwargs, env, .env, TOML, defaults.

    See :meth:`settings_customise_sources` for the layered source order.
    """

    model_config = SettingsConfigDict(
        env_prefix="HIVEGENT_",
        env_nested_delimiter="__",
        env_file=".env",
        env_file_encoding="utf-8",
    )

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        """Layer a TOML config file underneath env vars but above defaults.

        The path is read from ``HIVEGENT_CONFIG_FILE`` so deployments can
        relocate it without code changes; a missing file is silently empty.
        """
        toml_path = Path(os.environ.get(CONFIG_FILE_ENV_VAR, DEFAULT_CONFIG_FILE))
        return (
            init_settings,
            env_settings,
            dotenv_settings,
            TomlConfigSettingsSource(settings_cls, toml_file=toml_path),
            file_secret_settings,
        )

    llm: LlmSettings = LlmSettings()
    embedding: EmbeddingSettings = EmbeddingSettings()
    rerank: RerankSettings = RerankSettings()
    logfire: LogfireSettings = LogfireSettings()
    mcp: McpSettings = McpSettings()
    claims: ClaimSettings = ClaimSettings()
    auth: AuthSettings = AuthSettings()
    security: SecuritySettings = SecuritySettings()
    limits: LimitsSettings = LimitsSettings()
    network: NetworkSettings = NetworkSettings()
    db: DatabaseSettings = DatabaseSettings()

    data_dir: Path = Path("data")


settings = Settings()
