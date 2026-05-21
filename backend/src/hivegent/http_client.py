"""Process-wide HTTP client lifecycle.

The shared client is opened by the FastAPI lifespan and re-used by every
outbound caller (LLM, embeddings, JWKS, MCP, ``WebFetch``) so connection
pooling pays for itself and the SSRF-safe transport applies uniformly.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
from pydantic_ai.models import DEFAULT_HTTP_TIMEOUT, get_user_agent

from .config import settings
from .security import create_safe_async_client

__all__ = [
    "get_shared_http_client",
    "shared_http_client_lifespan",
]


class _SharedHttpClient:
    """Holder for the lifespan-managed process-wide HTTP client.

    Encapsulates the client reference as an instance attribute so the
    module binding stays constant — no ``global`` rebinding. The lifespan
    opens and closes the underlying client; access outside the lifespan
    raises so misuse (lazy first-touch on a stray event loop) surfaces
    immediately instead of silently binding to the wrong loop.
    """

    def __init__(self) -> None:
        self._client: httpx.AsyncClient | None = None

    @asynccontextmanager
    async def lifespan(self) -> AsyncIterator[httpx.AsyncClient]:
        if self._client is not None:
            raise RuntimeError(
                "shared HTTP client lifespan entered while already active"
            )
        client = create_safe_async_client(
            timeout=httpx.Timeout(
                timeout=DEFAULT_HTTP_TIMEOUT,
                connect=settings.network.connect_timeout_seconds,
            ),
            headers={"User-Agent": get_user_agent()},
        )
        self._client = client
        try:
            yield client
        finally:
            self._client = None
            await client.aclose()

    def get(self) -> httpx.AsyncClient:
        if self._client is None:
            raise RuntimeError(
                "Shared HTTP client is not initialised. Wrap the entrypoint in "
                "`shared_http_client_lifespan()` (the FastAPI lifespan does this "
                "for you)."
            )
        return self._client


_shared = _SharedHttpClient()


@asynccontextmanager
async def shared_http_client_lifespan() -> AsyncIterator[httpx.AsyncClient]:
    """Open the process-wide safe HTTP client for the duration of the context.

    Owned by the FastAPI lifespan: opens before the app accepts requests
    and closes after the last request has drained. Nested entries raise so
    overlapping ownership shows up as a hard error instead of a leaked
    pool.
    """
    async with _shared.lifespan() as client:
        yield client


def get_shared_http_client() -> httpx.AsyncClient:
    """Return the lifespan-managed shared HTTP client.

    Fails loudly when called outside :func:`shared_http_client_lifespan`
    so misuse surfaces immediately instead of silently binding the
    singleton to the wrong loop.
    """
    return _shared.get()
