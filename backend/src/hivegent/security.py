"""URL policy checks for outbound requests through the egress proxy.

The application validates URL shape and hostname policy on every request,
including redirects.
The egress proxy owns DNS resolution, public-address enforcement, and the
connection, which closes the DNS-rebinding window without a custom HTTP
transport.
"""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass

import httpx2

__all__ = [
    "DEFAULT_EGRESS_PROXY_URL",
    "UnsafeUrlError",
    "UrlPolicy",
    "create_safe_async_client",
    "require_safe_external_url",
    "require_safe_url_shape",
    "validate_external_headers",
]

DEFAULT_EGRESS_PROXY_URL = "http://127.0.0.1:4750"


class UnsafeUrlError(ValueError):
    """Raised when a URL or header fails an outbound safety check."""


def _host_matches(host: str, pattern: str) -> bool:
    """Whether *host* matches a hostname policy *pattern*."""
    if pattern == "*":
        return True

    host = host.lower().rstrip(".")
    pattern = pattern.lower().rstrip(".")

    return host == pattern or host.endswith("." + pattern)


@dataclass(slots=True, frozen=True)
class UrlPolicy:
    """Hostname allow and deny rules for untrusted outbound URLs.

    The deny list always wins.
    An empty allow list denies every host, while ``*`` permits every host that
    the egress proxy considers publicly routable.
    A domain entry matches that domain and all of its subdomains.
    """

    allow_hosts: tuple[str, ...] = ()
    deny_hosts: tuple[str, ...] = ()

    @property
    def has_allowlist(self) -> bool:
        """Whether the policy permits any host at all."""
        return bool(self.allow_hosts)

    def check_host(self, host: str) -> None:
        """Reject *host* unless it is explicitly allowed.

        Raises:
            UnsafeUrlError: If the host is denied by the policy.
        """
        if any(_host_matches(host, pattern) for pattern in self.deny_hosts):
            raise UnsafeUrlError(f"Host {host!r} is blocked by the URL host policy.")

        if not any(_host_matches(host, pattern) for pattern in self.allow_hosts):
            raise UnsafeUrlError(f"Host {host!r} is not on the URL host allowlist.")


def _check_url_shape(url: httpx2.URL) -> str:
    """Validate the scheme, credentials, and host of a parsed URL.

    Returns:
        The URL's host.
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


def _parsed_host(url: str) -> str:
    """Parse *url* and validate its shape.

    Returns:
        The URL's host.
    """
    if not url:
        raise UnsafeUrlError("URL is empty.")

    try:
        parsed = httpx2.URL(url)
    except (httpx2.InvalidURL, TypeError) as exc:
        raise UnsafeUrlError(f"Invalid URL: {exc}") from exc

    return _check_url_shape(parsed)


def _egress_transport(proxy_url: str) -> httpx2.AsyncBaseTransport:
    """Mint the proxied network transport for untrusted clients.

    The one construction site for the ``trust_env=False`` invariant, and the
    seam tests substitute to exercise the policy hook without a network.
    """
    return httpx2.AsyncHTTPTransport(proxy=proxy_url, trust_env=False)


def create_safe_async_client(
    *,
    policy: UrlPolicy,
    proxy_url: str,
    timeout: httpx2.Timeout | float | None,
    headers: Mapping[str, str] | None = None,
    auth: httpx2.Auth | None = None,
    follow_redirects: bool = False,
    max_redirects: int = 20,
) -> httpx2.AsyncClient:
    """Create a client that sends policy-checked requests through the proxy.

    The request hook runs for every redirect hop.  ``proxy_url`` is mandatory
    and the transport is built here, so an untrusted client cannot silently
    fall back to direct network access.
    """
    if not proxy_url:
        raise ValueError("An egress proxy URL is required for untrusted requests.")

    async def check_request(request: httpx2.Request) -> None:
        policy.check_host(_check_url_shape(request.url))

    return httpx2.AsyncClient(
        transport=_egress_transport(proxy_url),
        trust_env=False,
        event_hooks={"request": [check_request]},
        timeout=timeout,
        headers=headers,
        auth=auth,
        follow_redirects=follow_redirects,
        max_redirects=max_redirects,
    )


def validate_external_headers(
    headers: Iterable[tuple[str, str]] | Mapping[str, str],
) -> None:
    """Reject HTTP headers that contain CRLF or NUL characters.

    Raises:
        UnsafeUrlError: If any header name or value contains CR, LF, or NUL.
    """
    pairs = headers.items() if isinstance(headers, Mapping) else headers
    illegal = ("\r", "\n", "\x00")
    for name, value in pairs:
        if any(character in name for character in illegal) or any(
            character in value for character in illegal
        ):
            raise UnsafeUrlError(
                f"Header {name!r} contains illegal control characters."
            )


def require_safe_url_shape(url: str, label: str) -> None:
    """Validate URL shape for use inside Pydantic validators."""
    try:
        _parsed_host(url)
    except UnsafeUrlError as exc:
        raise ValueError(f"Unsafe {label}: {exc}") from exc


def require_safe_external_url(url: str, label: str, *, policy: UrlPolicy) -> None:
    """Apply the outbound URL policy and produce a labeled validation error."""
    try:
        policy.check_host(_parsed_host(url))
    except UnsafeUrlError as exc:
        raise ValueError(f"Unsafe {label}: {exc}") from exc
