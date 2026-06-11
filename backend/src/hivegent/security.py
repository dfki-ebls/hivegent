"""Shared URL safety helpers used by SSRF-sensitive code paths.

This module is settings-free: every check takes an explicit
:class:`UrlPolicy`, and the application settings are translated into
policies at the composition points (see ``SecuritySettings`` in
:mod:`hivegent.config`).

Enforcement has exactly one choke point per concern: the safe transport
checks URL shape and host policy on every request (covering each
redirect hop httpx follows), and its connect-time network backend
blocks private and reserved IPs after DNS resolution — the
resolved-address check that defends against DNS rebinding.  The
``validate_*`` helpers exist only to fail fast with clear errors at API
boundaries; the transport re-enforces everything they check.
"""

import asyncio
import ipaddress
import socket
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any, cast, override

import httpcore
import httpx

__all__ = [
    "TRUSTED_URL_POLICY",
    "SafeAsyncHTTPTransport",
    "UnsafeUrlError",
    "UrlPolicy",
    "create_safe_async_client",
    "is_safe_external_url",
    "require_safe_external_url",
    "require_safe_url_shape",
    "validate_external_headers",
    "validate_external_url_async",
]


class UnsafeUrlError(ValueError):
    """Raised when a URL or header fails the SSRF safety check."""


def _host_matches(host: str, pattern: str) -> bool:
    """Whether *host* matches a policy *pattern*.

    An ``example.com`` pattern matches ``example.com`` and any
    subdomain of it.
    """
    host = host.lower().rstrip(".")
    pattern = pattern.lower().rstrip(".")
    return host == pattern or host.endswith("." + pattern)


@dataclass(slots=True, frozen=True)
class UrlPolicy:
    """What a user- or model-supplied URL may dereference.

    ``allow_private`` opens the SSRF filter so URLs may dial private or
    loopback addresses.  ``allow_hosts`` and ``deny_hosts`` form the host
    policy: the deny list always wins, and a non-empty allow list refuses
    every host not on it (an empty allow list permits any host).  An
    ``example.com`` entry matches ``example.com`` and any of its
    subdomains.
    """

    allow_private: bool = False
    allow_hosts: tuple[str, ...] = ()
    deny_hosts: tuple[str, ...] = ()

    @property
    def restricts_hosts(self) -> bool:
        """Whether any host allow/deny rule is configured."""
        return bool(self.allow_hosts or self.deny_hosts)

    def check_host(self, host: str) -> None:
        """Enforce the allow/deny host rules on *host*.

        Raises:
            UnsafeUrlError: If the host is denied by the policy.
        """
        if any(_host_matches(host, p) for p in self.deny_hosts):
            raise UnsafeUrlError(f"Host {host!r} is blocked by the URL host policy.")
        if self.allow_hosts and not any(
            _host_matches(host, p) for p in self.allow_hosts
        ):
            raise UnsafeUrlError(f"Host {host!r} is not on the URL host allowlist.")


#: Policy for operator-configured endpoints: private addresses allowed,
#: no host restrictions.
TRUSTED_URL_POLICY = UrlPolicy(allow_private=True)


def _is_blocked_ip(addr: str) -> bool:
    ip_addr = ipaddress.ip_address(addr)
    return (
        ip_addr.is_private
        or ip_addr.is_reserved
        or ip_addr.is_loopback
        or ip_addr.is_link_local
        or ip_addr.is_multicast
        or ip_addr.is_unspecified
    )


async def _is_private_ip_async(host: str) -> bool:
    try:
        infos = await asyncio.to_thread(
            socket.getaddrinfo, host, None, proto=socket.IPPROTO_TCP
        )
    except socket.gaierror:
        return True
    return any(_is_blocked_ip(str(info[4][0])) for info in infos)


def _check_url_shape(url: httpx.URL) -> str:
    """Validate scheme, credentials, and host of a parsed URL.

    Returns:
        The URL's host.

    Raises:
        UnsafeUrlError: If the URL has a non-HTTP scheme, embedded
            credentials, or no host.
    """
    scheme = url.scheme.lower()
    if scheme not in ("http", "https"):
        raise UnsafeUrlError(
            f"URL scheme {scheme!r} is not allowed. Use http or https."
        )
    if url.userinfo:
        raise UnsafeUrlError("URLs with embedded credentials are not allowed.")
    if not url.host:
        raise UnsafeUrlError("URL has no host.")
    return url.host


def _parse_and_check_shape(url: str) -> str:
    if not url:
        raise UnsafeUrlError("URL is empty.")
    try:
        parsed = httpx.URL(url)
    except (httpx.InvalidURL, TypeError) as exc:
        raise UnsafeUrlError(f"Invalid URL: {exc}") from exc
    return _check_url_shape(parsed)


def is_safe_external_url(url: str, *, policy: UrlPolicy) -> bool:
    """Whether *url* passes the shape, host-policy, and literal-IP checks.

    A synchronous, DNS-free filter for URLs that are only surfaced (e.g.
    web search results) rather than dereferenced; the safe transport
    still enforces the full check before any fetch.
    """
    try:
        host = _parse_and_check_shape(url)
        policy.check_host(host)
    except UnsafeUrlError:
        return False
    if not policy.allow_private:
        try:
            return not _is_blocked_ip(host)
        except ValueError:
            pass  # Not an IP literal — DNS happens at fetch time.
    return True


class _SafeAsyncNetworkBackend(httpcore.AsyncNetworkBackend):
    """Blocks private and reserved addresses at TCP-connect time.

    The request-time check in :class:`SafeAsyncHTTPTransport` and this
    connect-time check can resolve a hostname to different addresses
    (DNS rebinding), so the peer IP is re-verified after connecting.
    """

    def __init__(self) -> None:
        # ``httpcore.AnyIOBackend`` is typed as a union of the real class
        # and a stub raised when anyio is missing; isinstance narrows back
        # to the abstract base for type checkers.
        backend = httpcore.AnyIOBackend()
        assert isinstance(backend, httpcore.AsyncNetworkBackend)
        self._backend = backend

    async def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options: Iterable[httpcore.SOCKET_OPTION] | None = None,
    ) -> httpcore.AsyncNetworkStream:
        if await _is_private_ip_async(host):
            raise httpcore.ConnectError(
                "URL resolves to a private or reserved IP address."
            )
        stream = await self._backend.connect_tcp(
            host,
            port,
            timeout=timeout,
            local_address=local_address,
            socket_options=socket_options,
        )
        peer = stream.get_extra_info("server_addr")
        if not peer or _is_blocked_ip(str(peer[0])):
            await stream.aclose()
            raise httpcore.ConnectError(
                "Connection reached a private or reserved IP address."
            )
        return stream

    async def connect_unix_socket(
        self,
        path: str,
        timeout: float | None = None,
        socket_options: Iterable[httpcore.SOCKET_OPTION] | None = None,
    ) -> httpcore.AsyncNetworkStream:
        raise httpcore.ConnectError("Unix sockets are not allowed for external URLs.")

    async def sleep(self, seconds: float) -> None:
        await self._backend.sleep(seconds)


class SafeAsyncHTTPTransport(httpx.AsyncBaseTransport):
    """HTTPX transport that enforces a :class:`UrlPolicy` on every request.

    URL shape and host policy are checked per request, which covers each
    redirect hop httpx follows; unless the policy allows private
    addresses, the connect-time backend additionally blocks private and
    reserved IPs after DNS resolution.  *inner* is the wrapped transport
    that performs the request once the checks pass; it defaults to a
    real network transport (tests substitute ``httpx.MockTransport`` to
    exercise the enforcement without the network).
    """

    def __init__(
        self,
        *,
        policy: UrlPolicy,
        inner: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        if inner is None:
            transport = httpx.AsyncHTTPTransport(trust_env=False)
            if not policy.allow_private:
                transport._pool._network_backend = _SafeAsyncNetworkBackend()  # pyright: ignore[reportPrivateUsage]  # ty: ignore[invalid-assignment]
            inner = transport
        self._policy = policy
        self._inner = inner

    @override
    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        host = _check_url_shape(request.url)
        self._policy.check_host(host)
        return await self._inner.handle_async_request(request)

    @override
    async def aclose(self) -> None:
        await self._inner.aclose()


def create_safe_async_client(
    *,
    policy: UrlPolicy,
    **kwargs: Any,
) -> httpx.AsyncClient:
    """Create an HTTPX async client with request- and connect-time SSRF protection.

    Pass :data:`TRUSTED_URL_POLICY` for operator-configured endpoints.
    """
    transport = SafeAsyncHTTPTransport(policy=policy)
    return httpx.AsyncClient(transport=transport, trust_env=False, **kwargs)


async def validate_external_url_async(url: str, *, policy: UrlPolicy) -> None:
    """Validate that *url* is safe to dereference, including a DNS check.

    The safe transport re-enforces all of this at request time; call
    this at API boundaries where an unsafe URL should fail fast with a
    clear error instead of a failed fetch later.
    """
    host = _parse_and_check_shape(url)
    policy.check_host(host)
    if not policy.allow_private and await _is_private_ip_async(host):
        raise UnsafeUrlError("URL resolves to a private or reserved IP address.")


def validate_external_headers(
    headers: Iterable[tuple[str, str]] | Mapping[str, str],
) -> None:
    """Reject HTTP headers that contain CRLF or other control characters.

    Raises:
        UnsafeUrlError: If any header name or value contains CR/LF/NUL.
    """
    pairs = (
        cast("Iterable[tuple[str, str]]", headers.items())
        if isinstance(headers, Mapping)
        else headers
    )
    illegal = ("\r", "\n", "\x00")
    for name, value in pairs:
        if any(ch in name for ch in illegal) or any(ch in value for ch in illegal):
            raise UnsafeUrlError(
                f"Header {name!r} contains illegal control characters."
            )


def require_safe_url_shape(url: str, label: str) -> None:
    """Validate scheme/host of *url* for use inside Pydantic validators.

    Does **not** perform DNS or apply the host policy — call
    :func:`validate_external_url_async` at the request boundary before
    dereferencing the URL. Converts :class:`UnsafeUrlError` into
    :class:`ValueError` so Pydantic produces a 422.
    """
    try:
        _parse_and_check_shape(url)
    except UnsafeUrlError as exc:
        raise ValueError(f"Unsafe {label}: {exc}") from exc


async def require_safe_external_url(
    url: str, label: str, *, policy: UrlPolicy
) -> None:
    """Async sibling of :func:`require_safe_url_shape` with the full check.

    Runs :func:`validate_external_url_async` (shape, host policy, DNS)
    and converts :class:`UnsafeUrlError` into a labeled
    :class:`ValueError` for API boundaries.
    """
    try:
        await validate_external_url_async(url, policy=policy)
    except UnsafeUrlError as exc:
        raise ValueError(f"Unsafe {label}: {exc}") from exc
