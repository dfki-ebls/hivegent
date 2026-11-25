"""Process-wide HTTP client lifecycle.

Shared clients are opened by the FastAPI lifespan and re-used by outbound
callers so connection pooling pays for itself.
The web client uses the SSRF-safe egress proxy, while a trusted client
connects directly to the origins of operator URLs from the settings and no
other, reaching the hosts in ``network.unix_sockets`` over their unix socket.
Pooling the web client is what keeps a research turn from paying a fresh
CONNECT and TLS handshake per tool call.
"""

from collections.abc import AsyncGenerator, Callable, Collection
from contextlib import asynccontextmanager
from dataclasses import dataclass, field

import httpx2
from pydantic_ai.models import DEFAULT_HTTP_TIMEOUT, get_user_agent

from .config import settings
from .lifespan import LifespanResource
from .security import (
    OperatorUrl,
    Origin,
    create_operator_client,
    create_safe_async_client,
)

__all__ = [
    "create_trusted_http_client",
    "get_trusted_http_client",
    "get_web_http_client",
    "shared_http_client_lifespan",
]


@dataclass(slots=True)
class _Clients:
    """The web client, and the direct clients opened on demand per set of operator URLs."""

    timeout: httpx2.Timeout
    web: httpx2.AsyncClient
    trusted: dict[frozenset[OperatorUrl], httpx2.AsyncClient] = field(
        default_factory=dict
    )


@asynccontextmanager
async def _open_clients() -> AsyncGenerator[_Clients]:
    """Open the web client now and close every client at the end."""
    network = settings.network
    timeout = httpx2.Timeout(
        timeout=DEFAULT_HTTP_TIMEOUT,
        connect=network.connect_timeout_seconds,
    )
    clients = _Clients(
        timeout=timeout,
        # Redirects are followed for fetched pages: every hop is checked by
        # the request hook and the egress proxy, so a moved page resolves
        # rather than failing. The hop limit is client-level in HTTPX, while
        # the tools bound each request as a whole.
        web=create_safe_async_client(
            policy=settings.security.web_policy(),
            proxy_url=settings.security.egress_proxy_url,
            timeout=timeout,
            follow_redirects=True,
            max_redirects=network.webfetch_max_redirects,
            keepalive_expiry=network.web_keepalive_seconds,
        ),
    )

    try:
        yield clients
    finally:
        for client in (clients.web, *clients.trusted.values()):
            await client.aclose()


_shared = LifespanResource(
    "Shared HTTP client", "shared_http_client_lifespan", _open_clients
)


@asynccontextmanager
async def shared_http_client_lifespan() -> AsyncGenerator[None]:
    """Open the process-wide safe HTTP clients for the duration of the context.

    Owned by the FastAPI lifespan: opens before the app accepts requests
    and closes after the last request has drained. Nested entries raise so
    overlapping ownership shows up as a hard error instead of a leaked pool.
    """
    async with _shared.lifespan():
        yield


def create_trusted_http_client(
    *urls: OperatorUrl,
    timeout: httpx2.Timeout,
    discovered: Callable[[], Collection[Origin]] = frozenset,
) -> httpx2.AsyncClient:
    """Create a direct client confined to the origins of the operator's *urls*.

    It reaches the hosts in ``network.unix_sockets`` over their socket, and
    refuses every request to another origin, so a caller can only reach what
    the operator configured, plus the *discovered* origins of the endpoints a
    document fetched from them names.  Its owner closes it, unlike a pooled one.
    """
    if not urls:
        raise ValueError("A trusted client needs an operator URL")

    return create_operator_client(
        urls,
        timeout=timeout,
        headers={"User-Agent": get_user_agent()},
        unix_sockets={
            host: str(path) for host, path in settings.network.unix_sockets.items()
        },
        discovered=discovered,
    )


def get_trusted_http_client(*urls: OperatorUrl) -> httpx2.AsyncClient:
    """Return the pooled :func:`create_trusted_http_client` of the operator's *urls*."""
    clients = _shared.get()
    key = frozenset(urls)

    if (client := clients.trusted.get(key)) is None:
        client = clients.trusted[key] = create_trusted_http_client(
            *key, timeout=clients.timeout
        )

    return client


def get_web_http_client() -> httpx2.AsyncClient:
    """Return the pooled client the model's web tools browse through."""
    return _shared.get().web
