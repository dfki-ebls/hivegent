"""Configuration settings for the hivegent application."""

import hashlib
import os
import re
import unicodedata
from enum import StrEnum
from pathlib import Path, PurePosixPath
from typing import Literal

from pydantic import BaseModel
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
    TomlConfigSettingsSource,
)

from .multimodal import BinaryContentMode
from .security import UrlPolicy

CONFIG_FILE_ENV_VAR = "HIVEGENT_CONFIG_FILE"
DEFAULT_CONFIG_FILE = Path("config.toml")

#: The fixed role name that grants administrator privileges.  Not
#: configurable: admin is a global capability, fully independent of the
#: groups used to share knowledge.
ADMIN_ROLE = "admin"

__all__ = [
    "ADMIN_ROLE",
    "AuthSettings",
    "ClaimSettings",
    "DatabaseSettings",
    "EmbeddingSettings",
    "InferenceProvider",
    "LimitsSettings",
    "LlmSettings",
    "LogfireSettings",
    "LoggingSettings",
    "McpSettings",
    "MultimodalSettings",
    "NetworkSettings",
    "RerankSettings",
    "SecuritySettings",
    "Settings",
    "UrlPolicySettings",
    "content_digest",
    "content_hash",
    "normalize_unicode",
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


# Only alphanumeric characters, underscores, and hyphens: an ID is a path
# segment, so anything else could traverse out of the workspace it names.
_SAFE_ID_PATTERN = re.compile(r"[a-zA-Z0-9_-]+")


def _sanitize_id(value: str, kind: str) -> str:
    """Return *value* unchanged if it is safe as a path segment.

    Args:
        value: The identifier to check.
        kind: What the identifier names, for the error message.

    Returns:
        The identifier, unchanged.

    Raises:
        ValueError: If the identifier is empty or contains unsafe characters.
    """
    if not value:
        raise ValueError(f"{kind} ID cannot be empty")

    if not _SAFE_ID_PATTERN.fullmatch(value):
        raise ValueError(f"Invalid {kind.lower()} ID: {value!r}")

    return value


def sanitize_user_id(user_id: str) -> str:
    """Sanitize a user ID to prevent path traversal attacks.

    Args:
        user_id: The user ID to sanitize.

    Returns:
        The sanitized user ID.

    Raises:
        ValueError: If the user ID is invalid or contains unsafe characters.
    """
    return _sanitize_id(user_id, "User")


def sanitize_group_id(group_id: str) -> str:
    """Sanitize a group ID to prevent path traversal attacks.

    The ID is the identity provider's stable handle for the group — it names
    the workspace directory, keys the SQL rows, and forms the ``@<id>``
    prefix of every path addressing it, so it is held to the same narrow
    alphabet as a user ID.

    Args:
        group_id: The group ID to sanitize.

    Returns:
        The sanitized group ID.

    Raises:
        ValueError: If the group ID is invalid or contains unsafe characters.
    """
    return _sanitize_id(group_id, "Group")


def normalize_unicode(value: str) -> str:
    """Return *value* in Unicode NFC, the canonical spelling for every path.

    Path strings only.  One visible filename has several byte spellings: a
    macOS upload carries ``U`` plus a combining diaeresis while a model can
    only ever emit the precomposed ``Ü``, so a path copied verbatim out of a
    listing still misses on a normalization-sensitive filesystem.  Folding
    every inbound path here gives disk, SQL, filters, and tool arguments one
    spelling to agree on.

    NFC because it is lossless and idempotent.  NFKC is neither: it would
    rewrite ``ﬁle.md`` to ``file.md``, silently renaming documents.

    Never applied to file content.  :mod:`hivegent.text` decodes user bytes
    without transforming them and :func:`content_digest` fingerprints that
    exact text, so normalizing content would invalidate every stored digest
    and every ``expected_hash`` token.

    Args:
        value: The path string to canonicalize.

    Returns:
        The NFC form of *value*.

    >>> normalize_unicode("SÜVOA.md")
    'SÜVOA.md'
    >>> normalize_unicode("ﬁle.md")
    'ﬁle.md'
    """
    return unicodedata.normalize("NFC", value)


def sanitize_document_path(path: str) -> str:
    """Sanitize a document path to prevent path traversal attacks.

    Normalizes the path to POSIX forward slashes and Unicode NFC, and rejects
    unsafe patterns.

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

    # Normalize to POSIX forward slashes and one canonical Unicode spelling
    normalized = str(PurePosixPath(normalize_unicode(path).replace("\\", "/")))

    if normalized.startswith("/"):
        raise ValueError("Document path must be relative")

    # Reject . and .. segments
    for segment in normalized.split("/"):
        if segment in (".", ".."):
            raise ValueError(f"Document path contains unsafe segment: {segment!r}")
        if not segment:
            raise ValueError("Document path contains empty segment")

    return normalized


class InferenceProvider(StrEnum):
    """Inference server providing the OpenAI-compatible LLM endpoint."""

    LLAMA_CPP = "llama.cpp"
    VLLM = "vllm"
    OPENAI = "openai"


class LlmSettings(BaseModel):
    """LLM provider defaults, configurable via environment variables.

    ``model`` drives the main chat agent, subagents (e.g. exploration),
    and conversation summarization for compaction, since those run with
    large contexts and tool calling where tiny models tend to fail.
    ``aux_model`` powers supplementary one-shot workloads — document
    conversion, alt-text and caption generation, title generation, and
    LLM-guided chunking — so it must be small, fast, and vision-capable.
    Those workloads call it many times per document (especially alt-text
    and chunking), so cost and latency dominate over raw reasoning
    quality.  All models share the same ``api_key`` and ``base_url``.

    ``stt_model`` names an audio transcription model (e.g. ``whisper-1``)
    served by the same OpenAI-compatible endpoint.  It backs the speech
    input fallback for browsers without a working Web Speech API; when
    unset, that fallback is disabled in the UI.

    ``max_tokens`` caps the completion length per tier.  ``aux_max_tokens``
    bounds the one-shot aux workloads (captions, titles, conversion): their
    outputs are short and the aux model is usually small-context, so an
    unbounded completion can fill the window before any answer is emitted.
    ``max_tokens`` (the main chat tier) defaults to ``None`` — open-ended,
    streamed answers should not be truncated — but is exposed so an operator
    can impose a ceiling.

    ``inference_provider`` identifies which OpenAI-compatible implementation
    serves the models.  Provider-specific request fields and model-profile
    overrides are derived from it centrally rather than configured
    individually.  It defaults to ``openai``, the strict reading: no
    non-standard request fields and no profile overrides, which is the only
    safe assumption for an endpoint that rejects what it does not know.  An
    operator pointing ``base_url`` at llama.cpp or vLLM opts into the
    compensation by naming it.

    The remaining knobs bound an agent run.  ``request_timeout_seconds`` is the
    per-request timeout applied to every model call (as ``ModelSettings.timeout``):
    for a non-streaming call it caps the whole call, and on a stream it detects a
    hung server (no bytes for that long) — continuous streaming resets it, so it
    does not cap total generation time.  ``tool_timeout_seconds`` bounds a single
    tool execution; on expiry pydantic-ai returns a retry prompt to the model, so
    a wedged tool (a stuck subprocess, query, or fetch) cannot stall a turn — set
    it generously so a legitimately slow tool (a subagent, a large-document
    conversion) still fits, or ``None`` to disable.  ``request_limit`` and
    ``tool_calls_limit`` bound how many model requests and tool calls a turn may
    make, shared across the main agent and its subagents (which run on the same
    usage accumulator), so an agentic loop terminates deterministically rather
    than only at pydantic-ai's implicit default of 50 requests;
    ``tool_calls_limit`` of ``None`` leaves tool calls uncapped.  ``retries`` is
    the per-run budget for re-prompting the model when a tool raises
    ``ModelRetry`` or output validation fails (applied to both); these retries
    count against ``request_limit``.  It is a global tolerance for every
    correctable tool failure, not just argument errors, and is above one so a
    single tool a model cannot get right on the first correction costs a turn
    rather than the whole run.

    ``caption_concurrency`` caps how many image-caption calls to ``aux_model``
    a single document issues at once; an image-heavy document would otherwise
    fan out one concurrent vision request per unique image, tripping provider
    rate limits so retries make it slower, not faster.

    ``subagent_timeout_seconds`` bounds one subagent delegation
    (``explore``); on expiry the partial findings are summarized and returned
    rather than failing the turn, so keep it below ``tool_timeout_seconds`` to
    fire first, or ``None`` to disable.  ``tool_output_max_chars`` caps the
    plain-text (LLM-facing) output of *any* tool, head+tail truncating a
    larger return while leaving its structured data intact; it is a coarse
    backstop on rendered size (which the built-in tools' own content caps do
    not bound), set above those caps so a considered read is not re-clamped
    but a runaway return — foreign or built-in — cannot dominate the context.
    """

    model: str = ""
    aux_model: str | None = None
    stt_model: str | None = None
    api_key: str = ""
    base_url: str = ""
    max_tokens: int | None = None
    aux_max_tokens: int | None = 2048
    inference_provider: InferenceProvider = InferenceProvider.OPENAI
    request_timeout_seconds: float = 600.0
    tool_timeout_seconds: float | None = 300.0
    request_limit: int = 40
    tool_calls_limit: int | None = 40
    caption_concurrency: int = 4
    subagent_timeout_seconds: float | None = 180.0
    tool_output_max_chars: int = 120_000
    retries: int = 2


class SummarizationSettings(BaseModel):
    """Transcript fidelity for conversation summarization.

    Applies to every summarization consumer — conversation compaction
    and subagent overflow recovery — so both produce summaries from the
    same view of a transcript.  The defaults include everything for
    maximum summary fidelity.  Summarization carries no token budget:
    the conversation just (nearly) fit the model's context window, and
    the short summary instructions usually leave enough headroom even
    for a full transcript.  Disabling the toggles shrinks the request
    when a deployment sees residual overflow errors instead.
    """

    include_tools: bool = True
    """Whether transcripts carry tool calls and their results."""

    include_reasoning: bool = True
    """Whether transcripts carry the assistant's reasoning parts."""


class MultimodalSettings(BaseModel):
    """How binary content reaches the chat model.

    ``binary_content`` governs the agent's binary reader
    (``read_binary_document``).  ``images`` (the default) rasterises PDFs
    to page images, the only multimodal content OpenAI-compatible vision
    servers (vLLM, SGLang, ...) accept; ``native`` forwards them as
    ``application/pdf`` for providers with first-class document
    understanding (OpenAI, Anthropic).  Set it to match the configured
    ``llm.model``'s capabilities.

    Ad-hoc chat attachments need no such policy: the composer accepts
    images alone (:data:`~hivegent.converters.INGESTIBLE_IMAGE_MEDIA_TYPES`),
    which every vision backend ingests identically.

    Configurable via ``HIVEGENT_MULTIMODAL__BINARY_CONTENT``.
    """

    binary_content: BinaryContentMode = BinaryContentMode.IMAGES


class LoggingSettings(BaseModel):
    """Standard-library logging settings.

    The root level applied by :func:`hivegent.logging_config.configure_logging`.
    Configurable via ``HIVEGENT_LOGGING__*`` environment variables.
    """

    level: str = "INFO"


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

    ``groups`` and ``roles`` are each a list of dotted claim paths whose
    entries are unioned.  Each defaults to reading both the top-level claim
    and a ``custom``-nested one, so a single backend serves interactive
    users (top-level ``groups``) and a service bot whose IdP can only nest
    static claims under ``custom.groups`` without any extra configuration.
    Override via ``HIVEGENT_CLAIMS__*`` with a JSON/TOML list.
    """

    groups: list[str] = ["groups", "custom.groups"]
    roles: list[str] = ["roles", "custom.roles"]
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

    ``frontend_client_id`` is the public OIDC client the browser SPA
    registers as; the backend serves it together with ``issuer`` from the
    ``GET /api/config`` endpoint so the frontend reads one source of truth
    rather than baking its own copy at build time.
    """

    enable: bool = True
    allow_disabled: bool = False
    issuer: str = ""
    audience: list[str] = []
    frontend_client_id: str = "hivegent-spa"
    jwks_cache_ttl: int = 3600
    jwks_force_refresh_min_interval_seconds: int = 60
    jwks_timeout_seconds: float = 10.0


class UrlPolicySettings(BaseModel):
    """Host allow/deny rules for one class of outbound URLs.

    An ``example.com`` entry matches ``example.com`` and any of its
    subdomains.  The deny list always wins; a non-empty allow list
    refuses every host not on it, while an empty allow list permits any
    host the SSRF filter accepts.
    """

    allow_hosts: list[str] = []
    deny_hosts: list[str] = []

    def to_policy(self, *, allow_private: bool) -> UrlPolicy:
        """Resolve into the runtime policy enforced by the security module."""
        return UrlPolicy(
            allow_private=allow_private,
            allow_hosts=tuple(self.allow_hosts),
            deny_hosts=tuple(self.deny_hosts),
        )


class SecuritySettings(BaseModel):
    """SSRF and transport-safety settings.

    ``allow_private_urls`` opens the SSRF filter so user-supplied URLs
    (LLM ``base_url``, MCP server URLs, ``web_fetch``) may dial private
    or loopback addresses. Default off, turn on only when authenticated
    users are allowed to reach the same network. Server-configured URLs
    are trusted operator input and do not need this setting.

    ``user_urls`` is the host policy applied to user-supplied endpoint
    URLs (user LLM ``base_url``, MCP server URLs); operator-configured
    URLs (the trusted HTTP client) bypass it.  ``web_urls``
    independently scopes what the model may browse with the web tools
    (``web_search`` results, ``web_fetch`` targets and redirect hops).
    The two policies do not inherit from each other, so a host barred
    from both goes on both deny lists.  The default web allow list is
    Wikipedia (every language edition, since an entry covers all of its
    subdomains), exactly the hosts the default ``wikipedia`` search
    engine can return — a
    curated, ad-free corpus that is safe to expose out of the box.  When
    both web lists are empty, the web tools are not registered at all:
    exposing the open web to the model needs an explicit operator
    decision, and search results that could never be fetched would be
    pointless.

    CORS, security headers, rate limiting, and body-size caps are
    enforced by the Caddy reverse proxy, not here.
    """

    allow_private_urls: bool = False
    expose_api_docs: bool = False
    user_urls: UrlPolicySettings = UrlPolicySettings()
    web_urls: UrlPolicySettings = UrlPolicySettings(allow_hosts=["wikipedia.org"])

    def user_policy(self) -> UrlPolicy:
        """Resolve the policy for user-supplied endpoint URLs."""
        return self.user_urls.to_policy(allow_private=self.allow_private_urls)

    def web_policy(self) -> UrlPolicy:
        """Resolve the policy for the model's web tools."""
        return self.web_urls.to_policy(allow_private=self.allow_private_urls)


class ToolsSettings(BaseModel):
    """Feature flags toggling the agent's built-in tools.

    Each flag is an operator master switch for one tool family, letting a
    deployment narrow what the model can do.  ``enable_web`` gates the
    ``web_search`` and ``web_fetch`` tools (which also need a
    ``security.web_urls`` policy to take effect); it is off by default, so
    the model answers from the indexed documents alone.  Add further
    flags here as more tool families gain a toggle.
    """

    enable_web: bool = False


class ComputeSettings(BaseModel):
    """Framework-agnostic compute tunables shared by every neural backend.

    Shared by every backend that runs torch/onnxruntime models — dense
    embeddings, the model-based chunkers, and the docling/marker/mineru
    converters.  Each backend maps these onto its own library and ignores
    what it cannot honor — CPU-only backends (kreuzberg, pdf_oxide)
    disregard ``batch_size`` entirely.

    Device *placement* is deliberately not configured here.  It is owned by
    the process environment (``CUDA_VISIBLE_DEVICES``), which every torch and
    onnxruntime consumer honors uniformly at the driver level, so each
    backend stays on its own ``auto`` detection and a single env var decides
    CPU vs GPU for all of them at once — set ``CUDA_VISIBLE_DEVICES=""`` to
    force CPU, ``"0"`` to pin a GPU.  This must be set before the process
    starts (e.g. on the systemd unit), since CUDA reads it once at init.

    ``num_threads`` caps the CPU-side intra-op / OpenMP threads; keep it
    well under the core count, as Tesseract's OpenMP scales sublinearly and
    the neural stages run on whatever device the environment exposes.
    ``batch_size`` is the page batch fed to the layout/table/OCR models;
    larger batches raise GPU utilization at the cost of VRAM.

    ``worker_processes`` >= 2 activates a persistent worker-process pool (see
    :mod:`hivegent.workers.pool`) so several documents convert and chunk in
    parallel, each worker holding its own models — a steady pool size, separate
    from and additive to the single-use isolation workers
    (:attr:`IsolationSettings.max_workers`).  Memory scales with the count, and
    ``num_threads`` is the *total* thread budget that :attr:`threads_per_worker`
    splits across the pool, so keep ``worker_processes`` near the core count with
    ``num_threads`` at or above it.  The default 1 keeps every stage in-process
    on a lock-guarded thread, spawning nothing — right for one core and tests.
    """

    num_threads: int = 8
    batch_size: int = 8
    worker_processes: int = 1

    @property
    def threads_per_worker(self) -> int:
        """Per-worker intra-op thread budget: ``num_threads`` split across the pool.

        Floored at 1, and simply ``num_threads`` when the pool is off.  The
        docling/marker/mineru/pdf-oxide accelerators read this to size their
        torch/onnxruntime pools, so processes never oversubscribe the cores.
        """
        return max(1, self.num_threads // max(1, self.worker_processes))


class OcrSettings(BaseModel):
    """Framework-agnostic OCR tunables shared by every OCR-capable backend.

    ``languages`` lists the Tesseract language codes (ISO 639-2, e.g.
    ``deu``, ``eng``); the matching ``*.traineddata`` packs must resolve
    through ``TESSDATA_PREFIX`` (wired up by the nix package and dev
    shell).  ``skip_native_text`` drops OCR for born-digital PDFs that
    already carry an extractable text layer (classified by pdf-inspector,
    see :func:`~hivegent.converters.pdf_classify.pdf_has_text_layer`);
    image-only/scanned PDFs fall through to OCR unchanged.
    """

    languages: list[str] = ["deu", "eng"]
    skip_native_text: bool = True


class ConversionSettings(BaseModel):
    """Framework-agnostic document conversion tunables.

    Holds the conversion-specific ``ocr`` knobs (languages, native-text
    skip); the threads/batch tunables live in the top-level
    :class:`ComputeSettings`, shared with embeddings and chunkers, and
    device placement is owned by the process environment (see there).

    ``libreoffice_images`` lets docling's Office backend rasterize embedded
    vector/legacy images it cannot decode with Pillow (DrawingML, VML, EMF,
    WMF) by cold-starting LibreOffice once per image and rendering the result
    through a throwaway PDF.  Those ``soffice`` invocations run serially and
    dominate conversion time on image-heavy documents, so it defaults off and
    such images degrade to placeholders.
    """

    ocr: OcrSettings = OcrSettings()
    libreoffice_images: bool = False


class LimitsSettings(BaseModel):
    """Upload, collection, and decoder size/count limits.

    ``max_image_pixels`` raises Pillow's decompression-bomb threshold
    so large embedded images inside PDFs (common with scanned pages)
    decode successfully; the value still guards against truly
    degenerate inputs.

    ``max_attachment_bytes`` bounds one image attached to a chat turn.  It
    sits well below ``max_file_size_bytes`` because an attachment rides in
    the conversation's message history and is re-sent to the model on every
    later turn, so its cost is paid per request rather than once.
    """

    max_file_size_bytes: int = 50 * 1024 * 1024  # 50 MB
    max_collection_size_bytes: int = 512 * 1024 * 1024  # 512 MB
    max_collection_files: int = 10_000
    max_image_pixels: int = 1_000_000_000  # ~3 GB uncompressed
    max_attachment_bytes: int = 10 * 1024 * 1024  # 10 MB


class JobSettings(BaseModel):
    """Background-job manager tunables.

    ``max_concurrency`` caps how many jobs (uploads, collections, bulk ops)
    convert and index at once; ``collection_concurrency`` is the inner cap on how
    many files one collection import processes at once, since a collection is a
    single job that would otherwise walk its files one at a time.  Both stay
    modest because the actual parallelism is bounded by
    :attr:`ComputeSettings.worker_processes` (the conversion/chunking pool) and
    the CPU-bound embedding stage.  ``retain_seconds`` is how long a finished job
    stays visible before it is pruned; ``queue_maxsize`` bounds each subscriber's
    snapshot queue so a stalled SSE consumer cannot leak memory.
    """

    max_concurrency: int = 3
    collection_concurrency: int = 4
    retain_seconds: float = 3600.0
    queue_maxsize: int = 1024


class IsolationSettings(BaseModel):
    """Single-use isolation-worker tunables (see :mod:`hivegent.workers`).

    These size the single-use, timeout-supervised policy
    (:func:`~hivegent.workers.isolation.run_isolated`), separate from and
    additive to the persistent pool (:attr:`ComputeSettings.worker_processes`).

    ``max_workers`` caps how many fresh isolation processes run at once across
    the server — a ceiling, not a steady pool size, since a process is spawned
    per crash-prone native call (currently pdfium paging) and torn down when it
    returns.  ``timeout_seconds`` is the per-call wall-clock limit before the
    worker is killed and the call raises ``WorkerTimeoutError``.
    """

    max_workers: int = 2
    timeout_seconds: float = 60.0


class DatabaseSettings(BaseModel):
    """Database backend configuration.

    ``url`` is a mandatory async SQLAlchemy URL (e.g.
    ``postgresql+psycopg://...``).  The only supported dialect is
    PostgreSQL with the ``pgvector`` extension; there is no fallback.
    """

    url: str = ""
    echo: bool = False


class NetworkSettings(BaseModel):
    """Outbound HTTP client and web-tool tunables.

    ``connect_timeout_seconds`` applies to every outbound request made
    through the shared HTTP client (LLM, embeddings, MCP, JWKS).  The
    ``webfetch_*`` knobs only apply to the ``web_fetch`` agent tool:
    ``webfetch_max_response_bytes`` caps how many raw bytes are
    downloaded per page, ``webfetch_max_chars`` caps the extracted text
    handed to the model, ``webfetch_max_line_chars`` truncates each
    numbered line so a data-URI or minified line cannot flood the
    context, and ``webfetch_max_formatted_chars`` bounds the rendered
    output as a whole, which neither of the other two does.  ``web_search`` queries the official
    Wikipedia API directly — no scraping, so no bot detection or rate
    limits — and only ever returns ``wikipedia.org`` links, matching the
    default ``web_urls`` allow list.  ``websearch_language`` selects the
    Wikipedia edition (e.g. ``en`` → en.wikipedia.org, ``de`` →
    de.wikipedia.org).  ``contact_email`` is the operator address put in
    the web tools' ``User-Agent`` (as Wikimedia's policy asks, so traffic
    questions reach a human); it falls back to the package author when
    unset.  The per-model-request timeout lives on ``LlmSettings``
    (``request_timeout_seconds``), not here.
    """

    connect_timeout_seconds: float = 5.0
    webfetch_timeout_seconds: float = 10.0
    webfetch_max_response_bytes: int = 5_000_000
    webfetch_max_chars: int = 100_000
    webfetch_max_line_chars: int = 2000
    webfetch_max_formatted_chars: int = 50_000
    webfetch_max_redirects: int = 5
    websearch_language: str = "en"
    contact_email: str = ""


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
    summarization: SummarizationSettings = SummarizationSettings()
    multimodal: MultimodalSettings = MultimodalSettings()
    embedding: EmbeddingSettings = EmbeddingSettings()
    rerank: RerankSettings = RerankSettings()
    logging: LoggingSettings = LoggingSettings()
    logfire: LogfireSettings = LogfireSettings()
    mcp: McpSettings = McpSettings()
    claims: ClaimSettings = ClaimSettings()
    auth: AuthSettings = AuthSettings()
    security: SecuritySettings = SecuritySettings()
    tools: ToolsSettings = ToolsSettings()
    compute: ComputeSettings = ComputeSettings()
    conversion: ConversionSettings = ConversionSettings()
    limits: LimitsSettings = LimitsSettings()
    network: NetworkSettings = NetworkSettings()
    jobs: JobSettings = JobSettings()
    isolation: IsolationSettings = IsolationSettings()
    db: DatabaseSettings = DatabaseSettings()

    data_dir: Path = Path("data")


settings = Settings()
