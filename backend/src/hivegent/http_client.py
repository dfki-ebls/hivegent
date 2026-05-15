"""Process-wide HTTP client lifecycle.

The shared client is opened by the FastAPI lifespan and re-used by every
outbound caller (LLM, embeddings, JWKS, MCP, ``WebFetch``) so connection
pooling pays for itself and the SSRF-safe transport applies uniformly.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
from pydantic_ai.models import DEFAULT_HTTP_TIMEOUT, get_user_agent

from .security import create_safe_async_client

__all__ = [
    "get_shared_http_client",
    "shared_http_client_lifespan",
]

_CONNECT_TIMEOUT_SECONDS = 5

_shared_client: httpx.AsyncClient | None = None


@asynccontextmanager
async def shared_http_client_lifespan() -> AsyncIterator[httpx.AsyncClient]:
    """Open the process-wide safe HTTP client for the duration of the context.

    Owned by the FastAPI lifespan: opens before the app accepts requests
    and closes after the last request has drained. Nested entries raise so
    overlapping ownership shows up as a hard error instead of a leaked
    pool.
    """
    global _shared_client
    if _shared_client is not None:
        raise RuntimeError("shared_http_client_lifespan entered while already active")
    client = create_safe_async_client(
        timeout=httpx.Timeout(
            timeout=DEFAULT_HTTP_TIMEOUT,
            connect=_CONNECT_TIMEOUT_SECONDS,
        ),
        headers={"User-Agent": get_user_agent()},
    )
    _shared_client = client
    try:
        yield client
    finally:
        _shared_client = None
        await client.aclose()


def get_shared_http_client() -> httpx.AsyncClient:
    """Return the lifespan-managed shared HTTP client.

    Fails loudly when called outside :func:`shared_http_client_lifespan`
    so misuse (lazy first-touch on a stray event loop) surfaces
    immediately instead of silently binding the singleton to the wrong
    loop.
    """
    if _shared_client is None:
        raise RuntimeError(
            "Shared HTTP client is not initialised. Wrap the entrypoint in "
            "`shared_http_client_lifespan()` (the FastAPI lifespan does this "
            "for you)."
        )
    return _shared_client
