"""Shared URL safety helpers used by SSRF-sensitive code paths."""

from __future__ import annotations

import asyncio
import ipaddress
import socket
from collections.abc import Iterable, Mapping
from typing import Any, cast

import httpcore
import httpx

from .config import settings

__all__ = [
    "SafeAsyncHTTPTransport",
    "UnsafeUrlError",
    "create_safe_async_client",
    "require_safe_url_shape",
    "validate_external_headers",
    "validate_external_url_async",
    "validate_optional_external_url",
]


class UnsafeUrlError(ValueError):
    """Raised when a URL or header fails the SSRF safety check."""


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

    host = str(parsed.host)
    if not host:
        raise UnsafeUrlError("URL has no host.")
    return host


def _resolve_allow_private(allow_private: bool | None) -> bool:
    return (
        settings.security.allow_private_urls if allow_private is None else allow_private
    )


class _SafeAsyncNetworkBackend(httpcore.AsyncNetworkBackend):
    """Rejects connections to private/reserved IPs at TCP-connect time.

    Defends against DNS rebinding: the boundary check at request time and
    the connect-time recheck here can resolve to different addresses.
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


class SafeAsyncHTTPTransport(httpx.AsyncHTTPTransport):
    """HTTPX transport that blocks private-address connections by default."""

    def __init__(self, *, allow_private: bool | None = None) -> None:
        super().__init__(trust_env=False)
        if not _resolve_allow_private(allow_private):
            self._pool._network_backend = _SafeAsyncNetworkBackend()  # pyright: ignore[reportPrivateUsage]  # ty: ignore[invalid-assignment]


def create_safe_async_client(
    *,
    allow_private: bool | None = None,
    **kwargs: Any,
) -> httpx.AsyncClient:
    """Create an HTTPX async client with connection-time SSRF protection."""
    transport = SafeAsyncHTTPTransport(allow_private=allow_private)
    return httpx.AsyncClient(transport=transport, trust_env=False, **kwargs)


async def validate_external_url_async(
    url: str, *, allow_private: bool | None = None
) -> None:
    """Validate that *url* is safe to dereference from async code."""
    host = _parse_and_check_scheme(url)
    if not _resolve_allow_private(allow_private) and await _is_private_ip_async(host):
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

    Does **not** perform DNS — call :func:`validate_external_url_async`
    at the request boundary before dereferencing the URL. Converts
    :class:`UnsafeUrlError` into :class:`ValueError` so Pydantic produces
    a 422.
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
