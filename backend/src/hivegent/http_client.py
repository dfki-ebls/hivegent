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
from .lifespan import LifespanResource
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


_Clients = dict[_HttpClientKind, httpx2.AsyncClient]


@asynccontextmanager
async def _open_clients() -> AsyncIterator[_Clients]:
    """Open one client per variant, keyed so they share one code path."""
    network = settings.network
    timeout = httpx2.Timeout(
        timeout=DEFAULT_HTTP_TIMEOUT,
        connect=network.connect_timeout_seconds,
    )
    headers = {"User-Agent": get_user_agent()}
    clients: _Clients = {
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
        yield clients
    finally:
        for client in clients.values():
            await client.aclose()


_shared = LifespanResource(
    "Shared HTTP client", "shared_http_client_lifespan", _open_clients
)


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
    return _shared.get()["trusted"]


def get_user_http_client() -> httpx2.AsyncClient:
    """Return the policy-checked proxy client for user-provided endpoints."""
    return _shared.get()["user"]


def get_web_http_client() -> httpx2.AsyncClient:
    """Return the pooled client the model's web tools browse through."""
    return _shared.get()["web"]
