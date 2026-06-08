"""Process-wide HTTP client lifecycle.

Shared clients are opened by the FastAPI lifespan and re-used by outbound
callers so connection pooling pays for itself. The user-policy client
enforces the SSRF filter; the trusted client allows operator-configured
private endpoints.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Literal

import httpx
from pydantic_ai.models import DEFAULT_HTTP_TIMEOUT, get_user_agent

from .config import settings
from .security import create_safe_async_client

__all__ = [
    "get_http_client",
    "shared_http_client_lifespan",
]

# Named client variants. Add a new kind here (and a matching entry in the
# lifespan) to introduce another specialized client later.
_HttpClientKind = Literal["user", "trusted"]


class _SharedHttpClients:
    """Holder for the lifespan-managed process-wide HTTP clients.

    Clients are keyed by variant so they share one code path, and the
    module binding stays constant — no ``global`` rebinding. The lifespan
    opens and closes them; access outside the lifespan raises so misuse
    (lazy first-touch on a stray event loop) surfaces immediately instead
    of binding to the wrong loop.
    """

    def __init__(self) -> None:
        self._clients: dict[_HttpClientKind, httpx.AsyncClient] | None = None

    @asynccontextmanager
    async def lifespan(self) -> AsyncIterator[None]:
        if self._clients is not None:
            raise RuntimeError(
                "shared HTTP client lifespan entered while already active"
            )
        timeout = httpx.Timeout(
            timeout=DEFAULT_HTTP_TIMEOUT,
            connect=settings.network.connect_timeout_seconds,
        )
        headers = {"User-Agent": get_user_agent()}
        # The user client passes ``allow_private=None`` (the default) so it
        # honours the global ``allow_private_urls`` toggle; the trusted client
        # always allows private addresses.
        self._clients = {
            "user": create_safe_async_client(timeout=timeout, headers=headers),
            "trusted": create_safe_async_client(
                allow_private=True, timeout=timeout, headers=headers
            ),
        }
        try:
            yield
        finally:
            clients = self._clients
            self._clients = None
            for client in clients.values():
                await client.aclose()

    def get(self, kind: _HttpClientKind) -> httpx.AsyncClient:
        if self._clients is None:
            raise RuntimeError(
                "Shared HTTP client is not initialised. Wrap the entrypoint in "
                "`shared_http_client_lifespan()` (the FastAPI lifespan does this "
                "for you)."
            )
        return self._clients[kind]


_shared = _SharedHttpClients()


@asynccontextmanager
async def shared_http_client_lifespan() -> AsyncIterator[None]:
    """Open the process-wide safe HTTP clients for the duration of the context.

    Owned by the FastAPI lifespan: opens before the app accepts requests
    and closes after the last request has drained. Nested entries raise so
    overlapping ownership shows up as a hard error instead of a leaked pool.
    """
    async with _shared.lifespan():
        yield


def get_http_client(*, allow_private: bool) -> httpx.AsyncClient:
    """Return the shared HTTP client matching the URL trust policy.

    ``allow_private`` selects the trusted client for operator-configured
    URLs; otherwise the user-policy client that enforces the SSRF filter.
    Fails loudly when called outside :func:`shared_http_client_lifespan` so
    misuse surfaces immediately instead of binding to the wrong loop.
    """
    return _shared.get("trusted" if allow_private else "user")
