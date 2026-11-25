"""URL policy checks for outbound requests through the egress proxy.

The application validates URL shape and hostname policy on every request,
including redirects.
The egress proxy owns DNS resolution, public-address enforcement, and the
connection, which closes the DNS-rebinding window without a custom HTTP
transport.
"""

from collections.abc import Awaitable, Callable, Collection, Mapping
from dataclasses import dataclass
from http.cookiejar import DefaultCookiePolicy
from typing import NewType

import httpx2

from .shared.l10n import Localized

__all__ = [
    "DEFAULT_EGRESS_PROXY_URL",
    "OperatorUrl",
    "Origin",
    "UnsafeUrlError",
    "UrlPolicy",
    "create_confined_client",
    "create_operator_client",
    "create_safe_async_client",
    "origin",
]

DEFAULT_EGRESS_PROXY_URL = "http://127.0.0.1:4750"

OperatorUrl = NewType("OperatorUrl", str)
"""An endpoint the operator configured, built only by the settings.

Only such a URL opens a direct client (:func:`create_operator_client`), which
an AST test keeps from being minted anywhere else.
"""

type Origin = tuple[str, str, int]
"""The scheme, host, and port of a URL, which a direct client is confined to."""

_DEFAULT_PORTS = {"http": 80, "https": 443}


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


def _origin_not_configured(url: str) -> Localized[str]:
    return Localized(
        en=f"{url!r} is outside the configured endpoint.",
        de=f"„{url}“ liegt außerhalb des konfigurierten Endpunkts.",
    )


class UnsafeUrlError(httpx2.RequestError):
    """Raised when a URL fails an outbound safety check, a refused request to its client."""


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


def origin(url: str | httpx2.URL) -> Origin:
    """The origin of an http or https *url*, its port spelled out.

    >>> origin("https://idp.example/realms/x")
    ('https', 'idp.example', 443)

    Raises:
        UnsafeUrlError: For another scheme, credentials, or no host.
    """
    parsed = httpx2.URL(url)
    host = _check_url_shape(parsed)
    scheme = parsed.scheme.lower()

    return scheme, host, parsed.port or _DEFAULT_PORTS[scheme]


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


def _checking(policy: UrlPolicy) -> Callable[[httpx2.Request], Awaitable[None]]:
    """The request hook refusing every URL of a bad shape or a host *policy* refuses."""

    async def check_request(request: httpx2.Request) -> None:
        policy.check_host(_check_url_shape(request.url))

    return check_request


def _confining(
    origins: Collection[Origin], discovered: Callable[[], Collection[Origin]]
) -> Callable[[httpx2.Request], Awaitable[None]]:
    """The request hook refusing every URL outside *origins* and the *discovered* ones."""

    async def check_request(request: httpx2.Request) -> None:
        target = origin(request.url)

        if target not in origins and target not in discovered():
            raise UnsafeUrlError(_origin_not_configured(str(request.url)).current)

    return check_request


def create_operator_client(
    urls: Collection[OperatorUrl],
    *,
    timeout: httpx2.Timeout,
    headers: Mapping[str, str],
    unix_sockets: Mapping[str, str],
    discovered: Callable[[], Collection[Origin]] = frozenset,
) -> httpx2.AsyncClient:
    """Create a direct client confined to the origins of the operator's *urls*.

    It ignores proxy variables and follows no redirect, and a host of
    *unix_sockets* is reached over its socket instead.  *discovered* adds the
    origins a document fetched from those origins names, read on every request.
    The client is shared across callers, so it keeps no cookies and a session
    one caller opened can never be inherited by another.
    """
    origins = frozenset(map(origin, urls))
    mounts: dict[str, httpx2.AsyncBaseTransport | None] = {
        f"all://{host}": httpx2.AsyncHTTPTransport(uds=path)
        for host, path in unix_sockets.items()
        if any(host == allowed for _, allowed, _ in origins)
    }

    return _cookieless(
        httpx2.AsyncClient(
            timeout=timeout,
            headers=headers,
            trust_env=False,
            follow_redirects=False,
            mounts=mounts,
            event_hooks={"request": [_confining(origins, discovered)]},
        )
    )


def _cookieless(client: httpx2.AsyncClient) -> httpx2.AsyncClient:
    """*client*, made to keep no cookies, since a shared client must never hand one caller's session to another."""
    client.cookies.jar.set_policy(DefaultCookiePolicy(allowed_domains=[]))

    return client


def create_confined_client(
    *,
    policy: UrlPolicy,
    timeout: httpx2.Timeout | float | None,
    auth: httpx2.Auth | None,
) -> httpx2.AsyncClient:
    """Create a direct client for an operator endpoint that reaches only the hosts *policy* allows.

    For code reaching an endpoint the operator named, such as a connector,
    which may lie on a private network the egress proxy refuses: every request
    is checked, no redirect is followed, and no proxy variable is honoured.
    """
    return httpx2.AsyncClient(
        trust_env=False,
        event_hooks={"request": [_checking(policy)]},
        timeout=timeout,
        auth=auth,
        follow_redirects=False,
    )


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
    The client is shared across callers, so it keeps no cookies.
    """
    if not proxy_url:
        raise ValueError("An egress proxy URL is required for untrusted requests.")

    return _cookieless(
        httpx2.AsyncClient(
            transport=_egress_transport(proxy_url, keepalive_expiry),
            trust_env=False,
            event_hooks={"request": [_checking(policy)]},
            timeout=timeout,
            headers=headers,
            auth=auth,
            follow_redirects=follow_redirects,
            max_redirects=max_redirects,
        )
    )
