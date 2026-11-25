"""Settings of the database and the outbound network."""

from pathlib import Path
from typing import Annotated

from pydantic import Field, NonNegativeInt, PositiveFloat, PositiveInt, SecretStr

from .common import Section

__all__ = [
    "DatabaseSettings",
    "NetworkSettings",
]


class DatabaseSettings(Section):
    """Database backend configuration.

    ``url`` is a mandatory async SQLAlchemy URL (e.g.
    ``postgresql+psycopg://...``).  The only supported dialect is
    PostgreSQL with the ``pgvector`` extension, there is no fallback.

    The pool keeps ``pool_size`` connections open, opens up to
    ``max_overflow`` more under load, and fails a checkout that waited
    ``pool_timeout_seconds`` for a free one.  The connections holding the
    server's session-level locks are opened beside it and take no slot.
    """

    url: SecretStr | None = None
    echo: bool = False
    pool_size: PositiveInt = 10
    max_overflow: NonNegativeInt = 10
    pool_timeout_seconds: PositiveFloat = 30.0


class NetworkSettings(Section):
    """Outbound HTTP client and web-tool tunables.

    ``connect_timeout_seconds`` applies to every outbound request made
    through the shared HTTP client (LLM, embeddings, MCP, JWKS).
    ``web_timeout_seconds`` bounds each ``web_search`` query and each
    ``web_fetch`` download as a whole, so a server trickling bytes cannot hold
    a turn.  The ``webfetch_*`` knobs only apply to the ``web_fetch`` agent tool:
    ``webfetch_max_response_bytes`` caps how many raw bytes are
    downloaded per page, and ``webfetch_fast_extraction`` skips trafilatura's backup
    extractors, saving CPU per page at the cost of some recall.
    ``contact_email`` is the operator address put in
    the ``web_fetch`` ``User-Agent``, so traffic questions reach a human, and
    none is sent while it is unset.  ``web_keepalive_seconds`` is
    how long the ``web_fetch`` client keeps
    an idle connection, long enough to outlast the model request between two
    tool calls of one turn.  The per-model-request timeout lives on ``LlmSettings``
    (``request_timeout_seconds``), not here.

    ``unix_sockets`` maps host names to unix socket paths, so an operator
    endpoint such as ``http://llmhop/v1`` keeps a plain URL while the trusted
    client connects to the socket instead of resolving the host.  It never
    applies to web URLs.
    """

    connect_timeout_seconds: float = 5.0
    web_timeout_seconds: float = 20.0
    webfetch_max_response_bytes: int = 5_000_000
    webfetch_fast_extraction: bool = False
    webfetch_max_redirects: int = 5
    web_keepalive_seconds: float = 60.0
    contact_email: Annotated[str, Field(min_length=1)] | None = None
    unix_sockets: dict[str, Path] = {}
