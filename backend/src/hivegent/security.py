"""Shared URL safety helpers used by SSRF-sensitive code paths.

This module is settings-free: every check takes an explicit
:class:`UrlPolicy`, and the application settings are translated into
policies at the composition points (see ``SecuritySettings`` in
:mod:`hivegent.config`).

Enforcement has exactly one choke point per concern: the safe transport
checks URL shape and host policy on every request (covering each
redirect hop HTTPX follows), then resolves the host and connects to the
validated address instead of the name.  Pinning the address is what
closes the DNS-rebinding window: the name cannot resolve to one address
for the check and another for the connect.  The ``validate_*`` helpers
exist only to fail fast with clear errors at API boundaries; the
transport re-enforces everything they check.
"""

import asyncio
import ipaddress
import socket
from collections import OrderedDict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any, cast, override

import httpx2

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


async def _resolve_public_addresses(host: str) -> list[str]:
    """Resolve *host* to addresses that are all public.

    Every address is checked, not just the one that ends up being used,
    so a name that mixes public and private records is refused outright.

    Returns:
        The resolved addresses, or *host* itself when it is already an
        IP literal.

    Raises:
        UnsafeUrlError: If the host does not resolve, or resolves to any
            private or reserved address.
    """
    try:
        blocked = _is_blocked_ip(host)
    except ValueError:
        pass  # Not an IP literal — resolve it below.
    else:
        if blocked:
            raise UnsafeUrlError("URL resolves to a private or reserved IP address.")

        return [host]

    try:
        infos = await asyncio.to_thread(
            socket.getaddrinfo, host, None, proto=socket.IPPROTO_TCP
        )
    except socket.gaierror as exc:
        raise UnsafeUrlError("URL host could not be resolved.") from exc

    addresses = [str(info[4][0]) for info in infos]
    if not addresses:
        raise UnsafeUrlError("URL host could not be resolved.")
    if any(_is_blocked_ip(address) for address in addresses):
        raise UnsafeUrlError("URL resolves to a private or reserved IP address.")

    return addresses


def _check_url_shape(url: httpx2.URL) -> str:
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
        parsed = httpx2.URL(url)
    except (httpx2.InvalidURL, TypeError) as exc:
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


def _new_transport() -> httpx2.AsyncHTTPTransport:
    """Mint a network transport that ignores ambient proxy environment.

    The one construction site for the ``trust_env=False`` invariant, and
    the seam tests substitute to exercise the pinning path without a
    network.
    """
    return httpx2.AsyncHTTPTransport(trust_env=False)


@dataclass(slots=True)
class _PinnedHost:
    """A host's chosen address and the connection pool pinned to it."""

    address: str
    transport: httpx2.AsyncHTTPTransport


#: How many hosts keep a live pinned pool.  The process-wide client
#: reaches model-supplied hosts, so without a bound the pools (and their
#: sockets) would accumulate for the lifetime of the server.
_MAX_PINNED_HOSTS = 32


class SafeAsyncHTTPTransport(httpx2.AsyncBaseTransport):
    """HTTPX transport that enforces a :class:`UrlPolicy` on every request.

    URL shape and host policy are checked per request, which covers each
    redirect hop HTTPX follows.  Unless the policy allows private
    addresses, the transport resolves the host and connects to the
    validated address while preserving the original HTTP host and TLS
    server name, which is what closes the DNS-rebinding window.

    Because the connection pool then keys on the pinned address, each
    host gets its own pool so hosts sharing an IP cannot reuse each
    other's TLS connections.  The chosen address is kept with the pool
    and re-used while it stays among the host's resolved addresses, so a
    rotating resolver does not force a fresh handshake per request.

    *inner* replaces the network transport outright, skipping pinning
    along with it; it exists for callers that already hold a transport,
    and for tests of the shape and host checks alone.
    """

    def __init__(
        self,
        *,
        policy: UrlPolicy,
        inner: httpx2.AsyncBaseTransport | None = None,
    ) -> None:
        self._policy = policy
        self._pin = inner is None and not policy.allow_private
        self._inner = inner or _new_transport()
        self._pinned: OrderedDict[str, _PinnedHost] = OrderedDict()

    async def _pinned_host(self, host: str) -> _PinnedHost:
        """Return the pool pinned to a still-valid address for *host*.

        Raises:
            UnsafeUrlError: If the host does not resolve to a public
                address.
        """
        addresses = await _resolve_public_addresses(host)
        pinned = self._pinned.pop(host, None)
        if pinned is not None:
            if pinned.address in addresses:
                self._pinned[host] = pinned  # Re-inserted as most recently used.

                return pinned

            await pinned.transport.aclose()

        while len(self._pinned) >= _MAX_PINNED_HOSTS:
            _, evicted = self._pinned.popitem(last=False)
            await evicted.transport.aclose()

        pinned = _PinnedHost(address=addresses[0], transport=_new_transport())
        self._pinned[host] = pinned

        return pinned

    @override
    async def handle_async_request(self, request: httpx2.Request) -> httpx2.Response:
        host = _check_url_shape(request.url)
        self._policy.check_host(host)
        if not self._pin:
            return await self._inner.handle_async_request(request)

        try:
            pinned = await self._pinned_host(host)
        except UnsafeUrlError as exc:
            raise httpx2.ConnectError(str(exc), request=request) from exc

        headers = request.headers.copy()
        headers["Host"] = request.url.netloc.decode("ascii")
        pinned_request = httpx2.Request(
            request.method,
            request.url.copy_with(host=pinned.address),
            headers=headers,
            stream=request.stream,
            extensions={**request.extensions, "sni_hostname": host},
        )

        return await pinned.transport.handle_async_request(pinned_request)

    @override
    async def aclose(self) -> None:
        pinned = list(self._pinned.values())
        self._pinned.clear()
        await asyncio.gather(
            self._inner.aclose(), *(entry.transport.aclose() for entry in pinned)
        )


def create_safe_async_client(
    *,
    policy: UrlPolicy,
    **kwargs: Any,
) -> httpx2.AsyncClient:
    """Create an HTTPX async client with request- and connect-time SSRF protection.

    Pass :data:`TRUSTED_URL_POLICY` for operator-configured endpoints.
    """
    transport = SafeAsyncHTTPTransport(policy=policy)
    return httpx2.AsyncClient(transport=transport, trust_env=False, **kwargs)


async def validate_external_url_async(url: str, *, policy: UrlPolicy) -> None:
    """Validate that *url* is safe to dereference, including a DNS check.

    The safe transport re-enforces all of this at request time; call
    this at API boundaries where an unsafe URL should fail fast with a
    clear error instead of a failed fetch later.
    """
    host = _parse_and_check_shape(url)
    policy.check_host(host)
    if not policy.allow_private:
        await _resolve_public_addresses(host)


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


async def require_safe_external_url(url: str, label: str, *, policy: UrlPolicy) -> None:
    """Async sibling of :func:`require_safe_url_shape` with the full check.

    Runs :func:`validate_external_url_async` (shape, host policy, DNS)
    and converts :class:`UnsafeUrlError` into a labeled
    :class:`ValueError` for API boundaries.
    """
    try:
        await validate_external_url_async(url, policy=policy)
    except UnsafeUrlError as exc:
        raise ValueError(f"Unsafe {label}: {exc}") from exc
