"""Shared URL safety helpers used by SSRF-sensitive code paths."""

import asyncio
import ipaddress
import socket
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any, cast

import httpcore
import httpx

from .config import settings

__all__ = [
    "TRUSTED_URL_POLICY",
    "SafeAsyncHTTPTransport",
    "UnsafeUrlError",
    "UrlPolicy",
    "create_safe_async_client",
    "is_safe_external_url",
    "require_safe_url_shape",
    "settings_url_policy",
    "validate_external_headers",
    "validate_external_url_async",
    "validate_optional_external_url",
    "web_url_policy",
]


class UnsafeUrlError(ValueError):
    """Raised when a URL or header fails the SSRF safety check."""


def _host_matches(host: str, pattern: str) -> bool:
    """Whether *host* matches a policy *pattern*.

    A plain pattern matches the hostname exactly; a ``*.example.com``
    pattern matches ``example.com`` and any subdomain of it.
    """
    host = host.lower().rstrip(".")
    pattern = pattern.lower().rstrip(".")
    if pattern.startswith("*."):
        base = pattern[2:]
        return host == base or host.endswith("." + base)
    return host == pattern


@dataclass(slots=True, frozen=True)
class UrlPolicy:
    """What a user- or model-supplied URL may dereference.

    ``allow_private`` opens the SSRF filter so URLs may dial private or
    loopback addresses.  ``allow_hosts`` and ``deny_hosts`` form the host
    policy: the deny list always wins, and a non-empty allow list refuses
    every host not on it (an empty allow list permits any host).  A plain
    entry matches a hostname exactly; a ``*.example.com`` entry also
    matches ``example.com`` and any of its subdomains.
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


def settings_url_policy() -> UrlPolicy:
    """Resolve the policy for user-supplied URLs from the application settings."""
    sec = settings.security
    return UrlPolicy(
        allow_private=sec.allow_private_urls,
        allow_hosts=tuple(sec.url_allow_hosts),
        deny_hosts=tuple(sec.url_deny_hosts),
    )


def web_url_policy() -> UrlPolicy:
    """Resolve the policy for the model's web tools from the application settings.

    Browsing is scoped by its own allow list (the global allow list does
    not apply) and inherits the global deny list on top of its own.
    """
    sec = settings.security
    return UrlPolicy(
        allow_private=sec.allow_private_urls,
        allow_hosts=tuple(sec.web_allow_hosts),
        deny_hosts=(*sec.url_deny_hosts, *sec.web_deny_hosts),
    )


def _resolve_policy(policy: UrlPolicy | None) -> UrlPolicy:
    return settings_url_policy() if policy is None else policy


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


def _parse_and_check_scheme(url: str) -> str:
    if not url:
        raise UnsafeUrlError("URL is empty.")
    try:
        parsed = httpx.URL(url)
    except (httpx.InvalidURL, TypeError) as exc:
        raise UnsafeUrlError(f"Invalid URL: {exc}") from exc

    scheme = str(parsed.scheme).lower()
    if scheme not in ("http", "https"):
        raise UnsafeUrlError(
            f"URL scheme {scheme!r} is not allowed. Use http or https."
        )

    if parsed.userinfo:
        raise UnsafeUrlError("URLs with embedded credentials are not allowed.")

    host = str(parsed.host)
    if not host:
        raise UnsafeUrlError("URL has no host.")
    return host


def is_safe_external_url(url: str, *, policy: UrlPolicy | None = None) -> bool:
    """Whether *url* passes the shape, host-policy, and literal-IP checks.

    A synchronous, DNS-free filter for URLs that are only surfaced (e.g.
    web search results) rather than dereferenced; the full async check
    still runs before any fetch.
    """
    policy = _resolve_policy(policy)
    try:
        host = _parse_and_check_scheme(url)
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
    """Rejects disallowed connections at TCP-connect time.

    Enforces the host policy and, unless the policy allows private
    addresses, blocks private/reserved IPs.  Defends against DNS
    rebinding: the boundary check at request time and the connect-time
    recheck here can resolve to different addresses.
    """

    def __init__(self, policy: UrlPolicy) -> None:
        # ``httpcore.AnyIOBackend`` is typed as a union of the real class
        # and a stub raised when anyio is missing; isinstance narrows back
        # to the abstract base for type checkers.
        backend = httpcore.AnyIOBackend()
        assert isinstance(backend, httpcore.AsyncNetworkBackend)
        self._backend = backend
        self._policy = policy

    async def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options: Iterable[httpcore.SOCKET_OPTION] | None = None,
    ) -> httpcore.AsyncNetworkStream:
        try:
            self._policy.check_host(host)
        except UnsafeUrlError as exc:
            raise httpcore.ConnectError(str(exc)) from exc
        if not self._policy.allow_private and await _is_private_ip_async(host):
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
        if not self._policy.allow_private:
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


class SafeAsyncHTTPTransport(httpx.AsyncHTTPTransport):
    """HTTPX transport that enforces a :class:`UrlPolicy` at connect time."""

    def __init__(self, *, policy: UrlPolicy | None = None) -> None:
        super().__init__(trust_env=False)
        policy = _resolve_policy(policy)
        if not policy.allow_private or policy.restricts_hosts:
            self._pool._network_backend = _SafeAsyncNetworkBackend(policy)  # pyright: ignore[reportPrivateUsage]  # ty: ignore[invalid-assignment]


def create_safe_async_client(
    *,
    policy: UrlPolicy | None = None,
    **kwargs: Any,
) -> httpx.AsyncClient:
    """Create an HTTPX async client with connection-time SSRF protection.

    *policy* defaults to the settings-derived user policy; pass
    :data:`TRUSTED_URL_POLICY` for operator-configured endpoints.
    """
    transport = SafeAsyncHTTPTransport(policy=policy)
    return httpx.AsyncClient(transport=transport, trust_env=False, **kwargs)


async def validate_external_url_async(
    url: str, *, policy: UrlPolicy | None = None
) -> None:
    """Validate that *url* is safe to dereference from async code."""
    policy = _resolve_policy(policy)
    host = _parse_and_check_scheme(url)
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
        _parse_and_check_scheme(url)
    except UnsafeUrlError as exc:
        raise ValueError(f"Unsafe {label}: {exc}") from exc


async def validate_optional_external_url(url: str | None, label: str) -> None:
    """Async SSRF check for an optional URL. No-op when *url* is falsy."""
    if not url:
        return
    try:
        await validate_external_url_async(url)
    except UnsafeUrlError as exc:
        raise ValueError(f"Unsafe {label}: {exc}") from exc
