"""URL policy checks for outbound requests through the egress proxy.

The application validates URL shape and hostname policy on every request,
including redirects.
The egress proxy owns DNS resolution, public-address enforcement, and the
connection, which closes the DNS-rebinding window without a custom HTTP
transport.
"""

from collections.abc import Mapping
from dataclasses import dataclass

import httpx2

from .l10n import Localized

__all__ = [
    "DEFAULT_EGRESS_PROXY_URL",
    "UnsafeUrlError",
    "UrlPolicy",
    "create_safe_async_client",
    "require_safe_external_url",
    "require_safe_headers",
    "require_safe_url_shape",
]

DEFAULT_EGRESS_PROXY_URL = "http://127.0.0.1:4750"


def _host_blocked(host: str) -> Localized[str]:
    return Localized(
        en=f"Host {host!r} is blocked by the URL host policy.",
        de=f"Der Host „{host}“ wird durch die URL-Host-Richtlinie blockiert.",
    )


def _host_not_allowed(host: str) -> Localized[str]:
    return Localized(
        en=f"Host {host!r} is not on the URL host allowlist.",
        de=f"Der Host „{host}“ steht nicht auf der Liste erlaubter URL-Hosts.",
    )


def _scheme_not_allowed(scheme: str) -> Localized[str]:
    return Localized(
        en=f"URL scheme {scheme!r} is not allowed. Use http or https.",
        de=f"Das URL-Schema „{scheme}“ ist nicht erlaubt. Verwende http oder https.",
    )


_CREDENTIALS = Localized(
    en="URLs with embedded credentials are not allowed.",
    de="URLs mit eingebetteten Zugangsdaten sind nicht erlaubt.",
)
_NO_HOST = Localized(en="URL has no host.", de="Die URL hat keinen Host.")
_EMPTY_URL = Localized(en="URL is empty.", de="Die URL ist leer.")


def _invalid_url(error: str) -> Localized[str]:
    return Localized(en=f"Invalid URL: {error}", de=f"Ungültige URL: {error}")


def _illegal_header(name: str) -> Localized[str]:
    return Localized(
        en=f"Header {name!r} contains illegal control characters.",
        de=f"Der Header „{name}“ enthält unzulässige Steuerzeichen.",
    )


def _unsafe_value(label: str, error: str) -> Localized[str]:
    return Localized(
        en=f"Unsafe {label}: {error}", de=f"Unsicherer Wert für {label}: {error}"
    )


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
            raise UnsafeUrlError(_host_blocked(host).current)

        if not any(_host_matches(host, pattern) for pattern in self.allow_hosts):
            raise UnsafeUrlError(_host_not_allowed(host).current)


def _check_url_shape(url: httpx2.URL) -> str:
    """Validate the scheme, credentials, and host of a parsed URL.

    Returns:
        The URL's host.
    """
    scheme = url.scheme.lower()
    if scheme not in ("http", "https"):
        raise UnsafeUrlError(_scheme_not_allowed(scheme).current)

    if url.userinfo:
        raise UnsafeUrlError(_CREDENTIALS.current)

    if not url.host:
        raise UnsafeUrlError(_NO_HOST.current)

    return url.host


def _parsed_host(url: str) -> str:
    """Parse *url* and validate its shape.

    Returns:
        The URL's host.
    """
    if not url:
        raise UnsafeUrlError(_EMPTY_URL.current)

    try:
        parsed = httpx2.URL(url)
    except (httpx2.InvalidURL, TypeError) as exc:
        raise UnsafeUrlError(_invalid_url(str(exc)).current) from exc

    return _check_url_shape(parsed)


def _egress_transport(
    proxy_url: str, keepalive_expiry: float
) -> httpx2.AsyncBaseTransport:
    """Mint the proxied network transport for untrusted clients.

    The one construction site for the ``trust_env=False`` invariant, and the
    seam tests substitute to exercise the policy hook without a network.
    """
    limits = httpx2.Limits(
        max_connections=100,
        max_keepalive_connections=20,
        keepalive_expiry=keepalive_expiry,
    )

    return httpx2.AsyncHTTPTransport(proxy=proxy_url, trust_env=False, limits=limits)


def create_safe_async_client(
    *,
    policy: UrlPolicy,
    proxy_url: str,
    timeout: httpx2.Timeout | float | None,
    headers: Mapping[str, str] | None = None,
    auth: httpx2.Auth | None = None,
    follow_redirects: bool = False,
    max_redirects: int = 20,
    keepalive_expiry: float = 5.0,
) -> httpx2.AsyncClient:
    """Create a client that sends policy-checked requests through the proxy.

    The request hook runs for every redirect hop.  ``proxy_url`` is mandatory
    and the transport is built here, so an untrusted client cannot silently
    fall back to direct network access.  *keepalive_expiry* is how long an
    idle pooled connection is kept, the pool sizes being HTTPX's defaults.
    """
    if not proxy_url:
        raise ValueError("An egress proxy URL is required for untrusted requests.")

    async def check_request(request: httpx2.Request) -> None:
        policy.check_host(_check_url_shape(request.url))

    return httpx2.AsyncClient(
        transport=_egress_transport(proxy_url, keepalive_expiry),
        trust_env=False,
        event_hooks={"request": [check_request]},
        timeout=timeout,
        headers=headers,
        auth=auth,
        follow_redirects=follow_redirects,
        max_redirects=max_redirects,
    )


def require_safe_headers(headers: Mapping[str, str], label: str) -> None:
    """Reject HTTP headers that contain CR, LF, or NUL characters.

    Raises:
        ValueError: Naming *label*, for use inside Pydantic validators.
    """
    illegal = ("\r", "\n", "\x00")
    for name, value in headers.items():
        if any(character in name or character in value for character in illegal):
            raise ValueError(
                _unsafe_value(label, _illegal_header(name).current).current
            )


def require_safe_url_shape(url: str, label: str) -> None:
    """Validate URL shape for use inside Pydantic validators."""
    try:
        _parsed_host(url)
    except UnsafeUrlError as exc:
        raise ValueError(_unsafe_value(label, str(exc)).current) from exc


def require_safe_external_url(url: str, label: str, *, policy: UrlPolicy) -> None:
    """Apply the outbound URL policy and produce a labeled validation error."""
    try:
        policy.check_host(_parsed_host(url))
    except UnsafeUrlError as exc:
        raise ValueError(_unsafe_value(label, str(exc)).current) from exc
