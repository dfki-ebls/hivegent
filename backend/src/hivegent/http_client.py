"""Process-wide HTTP client lifecycle.

Shared clients are opened by the FastAPI lifespan and re-used by outbound
callers so connection pooling pays for itself.
The user-policy and web clients use the SSRF-safe egress proxy, while the
trusted client connects directly to operator-configured endpoints.
The two proxied clients are separate because ``user_urls`` and ``web_urls``
are independent policies, and pooling the web client is what keeps a research
turn from paying a fresh CONNECT and TLS handshake per tool call.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Literal

import httpx2
from pydantic_ai.models import DEFAULT_HTTP_TIMEOUT, get_user_agent

from .config import settings
from .security import create_safe_async_client

__all__ = [
    "get_trusted_http_client",
    "get_user_http_client",
    "get_web_http_client",
    "shared_http_client_lifespan",
]

# Named client variants. Add a new kind here (and a matching entry in the
# lifespan) to introduce another specialized client later.
_HttpClientKind = Literal["user", "trusted", "web"]


class _SharedHttpClients:
    """Holder for the lifespan-managed process-wide HTTP clients.

    Clients are keyed by variant so they share one code path, and the
    module binding stays constant — no ``global`` rebinding. The lifespan
    opens and closes them; access outside the lifespan raises so misuse
    (lazy first-touch on a stray event loop) surfaces immediately instead
    of binding to the wrong loop.
    """

    def __init__(self) -> None:
        self._clients: dict[_HttpClientKind, httpx2.AsyncClient] | None = None

    @asynccontextmanager
    async def lifespan(self) -> AsyncIterator[None]:
        if self._clients is not None:
            raise RuntimeError(
                "shared HTTP client lifespan entered while already active"
            )
        network = settings.network
        timeout = httpx2.Timeout(
            timeout=DEFAULT_HTTP_TIMEOUT,
            connect=network.connect_timeout_seconds,
        )
        headers = {"User-Agent": get_user_agent()}
        self._clients = {
            "user": create_safe_async_client(
                policy=settings.security.user_policy(),
                proxy_url=settings.security.egress_proxy_url,
                timeout=timeout,
                headers=headers,
            ),
            "trusted": httpx2.AsyncClient(
                timeout=timeout,
                headers=headers,
                trust_env=False,
            ),
            # Redirects are followed for both web tools: every hop is checked
            # by the request hook and the egress proxy, so a search that lands
            # on a redirecting Wikipedia language alias resolves rather than
            # failing. The hop limit is client-level in HTTPX either way.
            "web": create_safe_async_client(
                policy=settings.security.web_policy(),
                proxy_url=settings.security.egress_proxy_url,
                timeout=httpx2.Timeout(
                    timeout=network.webfetch_timeout_seconds,
                    connect=network.connect_timeout_seconds,
                ),
                follow_redirects=True,
                max_redirects=network.webfetch_max_redirects,
            ),
        }
        try:
            yield
        finally:
            clients = self._clients
            self._clients = None
            for client in clients.values():
                await client.aclose()

    def get(self, kind: _HttpClientKind) -> httpx2.AsyncClient:
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


def get_trusted_http_client() -> httpx2.AsyncClient:
    """Return the direct client for operator-configured endpoints."""
    return _shared.get("trusted")


def get_user_http_client() -> httpx2.AsyncClient:
    """Return the policy-checked proxy client for user-provided endpoints."""
    return _shared.get("user")


def get_web_http_client() -> httpx2.AsyncClient:
    """Return the pooled client the model's web tools browse through."""
    return _shared.get("web")
