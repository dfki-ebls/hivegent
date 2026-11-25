"""Settings of authentication, claims, and URL policies."""

from typing import Literal

from ..security import DEFAULT_EGRESS_PROXY_URL, OperatorUrl, UrlPolicy
from .common import HttpOperatorUrl, HttpsOperatorUrl, Section

__all__ = [
    "AuthSettings",
    "ClaimSettings",
    "SecuritySettings",
    "UrlPolicySettings",
]


class ClaimSettings(Section):
    """OIDC claim names and how their group entries are interpreted.

    Configurable via ``HIVEGENT_CLAIMS__*`` environment variables.

    ``groups`` is the claim carrying shared-knowledge group memberships
    (entries like ``"engineering:write"``); ``roles`` is the claim
    carrying global roles (e.g. the fixed :data:`ADMIN_ROLE`).
    ``default_group_permission`` is the permission granted to a bare
    group entry (one without a ``:read``/``:write`` suffix).

    ``groups`` and ``roles`` are each a list of claim paths whose entries
    are unioned, a path being the keys that lead to the claim through nested
    objects, such as ``["custom", "groups"]`` for the static claims a
    Rauthy service client nests under ``custom``.  Each defaults to its
    top-level claim alone.
    """

    groups: list[list[str]] = [["groups"]]
    roles: list[list[str]] = [["roles"]]
    default_group_permission: Literal["read", "write"] = "write"


class AuthSettings(Section):
    """OIDC authentication settings.

    Configurable via ``HIVEGENT_AUTH__*`` environment variables.

    ``audience`` is the set of accepted token audiences.  The IdP stamps
    each token's ``aud`` with the issuing client's id, so this confines
    the API to its own clients, each named exactly.  A token whose ``aud``
    names none of them is rejected.  Required with an ``issuer``.
    ``jwks_timeout_seconds`` caps both the OIDC discovery and JWKS fetches.
    ``jwks_max_age_seconds`` caps how long the keys of the last successful
    fetch stay trusted while the issuer is unreachable.

    ``hivegent serve`` requires an ``issuer``, and ``hivegent dev`` refuses one,
    since every request there acts as a development user.

    ``frontend_client_id`` is the public OIDC client the browser SPA
    registers as; the backend serves it together with ``issuer`` from the
    ``GET /api/v1/config`` endpoint so the frontend reads one source of truth
    rather than baking its own copy at build time.

    ``service_client_id`` names the client of the Teams bridge
    (``hivegent-bot``), whose tokens (``azp`` or ``client_id``) act as a
    service principal: it reads the global workspace alone, only ever in
    read mode, and reaches nothing but the chat route.  Unset, no client
    is one.
    """

    issuer: HttpsOperatorUrl | None = None
    audience: list[str] = []
    frontend_client_id: str = "hivegent-spa"
    service_client_id: str | None = None
    jwks_cache_ttl: int = 3600
    jwks_force_refresh_min_interval_seconds: int = 60
    jwks_timeout_seconds: float = 10.0
    jwks_max_age_seconds: int = 21600

    @property
    def enabled(self) -> bool:
        """Whether tokens are validated, which an ``issuer`` turns on."""
        return self.issuer is not None


class UrlPolicySettings(Section):
    """Host allow/deny rules for one class of outbound URLs.

    An ``example.com`` entry matches ``example.com`` and any of its
    subdomains.  The deny list always wins, an empty allow list denies
    every host, and ``*`` allows every publicly routable host.
    """

    allow_hosts: list[str] = []
    deny_hosts: list[str] = []

    def to_policy(self) -> UrlPolicy:
        """Resolve into the runtime policy enforced by the security module."""
        return UrlPolicy(
            allow_hosts=tuple(self.allow_hosts),
            deny_hosts=tuple(self.deny_hosts),
        )


class SecuritySettings(Section):
    """Outbound URL and transport-safety settings.

    Model-controlled traffic passes through ``egress_proxy_url``.
    The proxy resolves destinations and rejects non-public addresses, while
    the application enforces the hostname allowlist on every request and
    redirect.
    Operator-configured endpoints use a separate direct client.

    ``web_urls`` scopes what ``web_fetch`` may open, including redirect hops,
    and its empty default leaves the tool unregistered.
    ``*`` lets the model read any public page the user or a search named,
    and a set of domains confines it to those.

    CORS, security headers, and body-size caps are enforced by the Caddy
    reverse proxy, while per-user rate limits are ``rate_limits``, since only
    the backend knows the user.
    """

    egress_proxy_url: HttpOperatorUrl = OperatorUrl(DEFAULT_EGRESS_PROXY_URL)
    expose_api_docs: bool = False
    web_urls: UrlPolicySettings = UrlPolicySettings()

    def web_policy(self) -> UrlPolicy:
        """Resolve the policy for the model's web tools."""
        return self.web_urls.to_policy()
