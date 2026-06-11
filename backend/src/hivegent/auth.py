"""OIDC authentication and JWT validation."""

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable, Iterator, Mapping
from typing import Annotated, Any

import httpx
from fastapi import Depends, Header, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from fastmcp.server.auth.oidc_proxy import OIDCConfiguration
from joserfc import jwt
from joserfc.errors import (
    DecodeError,
    ExpiredTokenError,
    InvalidClaimError,
    InvalidKeyIdError,
    JoseError,
    MissingClaimError,
)
from joserfc.jwk import KeySet
from joserfc.jws import extract_compact
from joserfc.jwt import ClaimsOption, JWTClaimsRegistry
from pydantic import AnyHttpUrl, ValidationError

from .config import ADMIN_ROLE, sanitize_user_id, settings
from .db.groups import list_group_ids
from .db.users import user_exists
from .http_client import get_http_client
from .types import User

__all__ = [
    "DEFAULT_JWT_ALGORITHMS",
    "IMPERSONATE_HEADER",
    "User",
    "build_discovery_url",
    "fetch_oidc_configuration",
    "get_current_user",
    "parse_group_claim",
    "require_admin",
]

logger = logging.getLogger(__name__)

# Request header through which an admin impersonates another user.
IMPERSONATE_HEADER = "X-Impersonate-User"

# Fallback when the IdP's discovery document doesn't advertise
# ``id_token_signing_alg_values_supported`` and no explicit override is set.
# Modern Ed25519-based signatures only — no legacy RSA/ECDSA algorithms.
# joserfc spells the JWS alg ``EdDSA`` and the JWK ``crv`` value ``Ed25519``;
# both names appear in the wild, so accept either.
DEFAULT_JWT_ALGORITHMS: tuple[str, ...] = ("EdDSA", "Ed25519")


def build_discovery_url(issuer: str) -> str:
    """Return the RFC 8414 / OIDC Discovery 1.0 URL for ``issuer``."""
    return f"{issuer.rstrip('/')}/.well-known/openid-configuration"


def fetch_oidc_configuration(
    issuer: str, *, timeout_seconds: float | None = None
) -> OIDCConfiguration:
    """Fetch and parse the OIDC discovery document for ``issuer``.

    Delegates to fastmcp's ``OIDCConfiguration.get_oidc_configuration``
    so the FastAPI and FastMCP auth layers share one implementation.

    Raises:
        httpx.HTTPError: On transport failure.
        pydantic.ValidationError: On malformed payload.
        ValueError: If ``issuer`` is empty.
    """
    if not issuer:
        raise ValueError("OIDC issuer not configured")
    config_url = AnyHttpUrl(build_discovery_url(issuer))
    timeout = (
        settings.auth.jwks_timeout_seconds
        if timeout_seconds is None
        else timeout_seconds
    )
    return OIDCConfiguration.get_oidc_configuration(
        config_url, strict=False, timeout_seconds=int(timeout)
    )


class _SingleFlightCache[T]:
    """TTL cache whose refreshes are single-flighted with stale fallback.

    Concurrent cache misses share one refresh, and a failed refresh
    serves the previously cached value instead of failing the caller —
    re-stamping the TTL so the next retry is a full interval away.
    """

    def __init__(self, name: str) -> None:
        self._name = name
        self._value: T | None = None
        self._time: float = 0
        self._lock = asyncio.Lock()

    def _fresh(self, *, force_refresh: bool) -> T | None:
        if self._value is None:
            return None
        ttl = (
            settings.auth.jwks_force_refresh_min_interval_seconds
            if force_refresh
            else settings.auth.jwks_cache_ttl
        )
        return self._value if time.time() - self._time < ttl else None

    async def get(
        self, refresh: Callable[[], Awaitable[T]], *, force_refresh: bool
    ) -> T:
        """Return the cached value, refreshing it through ``refresh`` on expiry.

        Raises:
            Exception: Whatever ``refresh`` raised, only when no cached
                value is available to fall back to.
        """
        if (fresh := self._fresh(force_refresh=force_refresh)) is not None:
            return fresh
        async with self._lock:
            if (fresh := self._fresh(force_refresh=force_refresh)) is not None:
                return fresh
            try:
                value = await refresh()
            except Exception as e:
                if self._value is None:
                    raise
                logger.warning(
                    "%s refresh failed, serving cached copy: %s", self._name, e
                )
                value = self._value
            self._value = value
            self._time = time.time()
            return value


class JWKSFetcher:
    """Fetch and cache JWKS from OIDC provider via discovery.

    The discovery document and the key set each live in a
    :class:`_SingleFlightCache`: concurrent cache misses share one
    request, and a transient IdP hiccup serves the cached value instead
    of failing authenticated requests.  Key-rotation safety is preserved
    by the kid-miss force-refresh path in :func:`validate_jwt_token`.
    """

    def __init__(self) -> None:
        self._discovery = _SingleFlightCache[OIDCConfiguration]("OIDC discovery")
        self._jwks = _SingleFlightCache[KeySet]("JWKS")

    @staticmethod
    async def _fetch_discovery() -> OIDCConfiguration:
        """Fetch and validate the OIDC discovery document.

        Raises:
            HTTPException: 503 on transport failure, 500 on malformed payload
                or missing configuration.
        """
        try:
            config = await asyncio.to_thread(
                fetch_oidc_configuration, settings.auth.issuer
            )
        except httpx.HTTPError as e:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=f"Failed to fetch OIDC discovery document: {e}",
            ) from e
        except (ValidationError, ValueError) as e:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Invalid OIDC discovery document: {e}",
            ) from e
        if not config.jwks_uri:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="OIDC discovery document missing jwks_uri",
            )
        return config

    async def _get_discovery(self, force_refresh: bool = False) -> OIDCConfiguration:
        """Fetch the OIDC discovery document.

        Raises:
            HTTPException: 503 on transport failure, 500 on malformed payload
                or missing configuration — only when no cached document is
                available to fall back to.
        """
        return await self._discovery.get(
            self._fetch_discovery, force_refresh=force_refresh
        )

    async def get_allowed_algorithms(self, force_refresh: bool = False) -> list[str]:
        """Return the list of JWT signing algorithms accepted for tokens.

        Reads ``id_token_signing_alg_values_supported`` from the OIDC
        discovery document (rauthy and most IdPs advertise the same algs
        for ID and access tokens), falling back to
        ``DEFAULT_JWT_ALGORITHMS`` if the field is absent.

        Raises:
            HTTPException: If discovery has to be fetched and fails.
        """
        config = await self._get_discovery(force_refresh=force_refresh)
        advertised = config.id_token_signing_alg_values_supported
        if advertised:
            return list(advertised)
        return list(DEFAULT_JWT_ALGORITHMS)

    async def _fetch_jwks(self, force_refresh: bool) -> KeySet:
        """Fetch the key set from the ``jwks_uri`` advertised by discovery.

        Raises:
            HTTPException: 503 on transport failure, 500 on a malformed
                key set, or whatever discovery itself raised.
        """
        config = await self._get_discovery(force_refresh=force_refresh)
        try:
            response = await get_http_client(allow_private=True).get(
                str(config.jwks_uri), timeout=settings.auth.jwks_timeout_seconds
            )
            response.raise_for_status()
            jwks_data = response.json()
        except httpx.HTTPError as e:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=f"Failed to fetch JWKS: {e}",
            ) from e
        try:
            return KeySet.import_key_set(jwks_data)
        except Exception as e:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Invalid JWKS format: {e}",
            ) from e

    async def get_jwks(self, force_refresh: bool = False) -> KeySet:
        """Fetch JWKS from the OIDC provider, resolving the URI via discovery.

        Raises:
            HTTPException: If JWKS cannot be fetched and no cached key set
                is available to fall back to.
        """
        return await self._jwks.get(
            lambda: self._fetch_jwks(force_refresh), force_refresh=force_refresh
        )


_jwks_fetcher = JWKSFetcher()


def _should_refresh_jwks(token: str, key_set: KeySet) -> bool:
    """Refresh JWKS only when the token names a key id absent from the cache.

    Reading the header is safe because the signature isn't trusted yet — a
    mismatched ``kid`` is the only signal that key rotation, rather than
    tampering, caused the verification failure.
    """
    try:
        header = extract_compact(token.encode("ascii")).headers()
    except (DecodeError, UnicodeEncodeError):
        return False
    kid = header.get("kid")
    if not isinstance(kid, str) or not kid:
        return False
    try:
        key_set.get_by_kid(kid)
    except InvalidKeyIdError:
        return True
    return False


def _audience_matches(aud: str) -> bool:
    """Whether a token ``aud`` matches any configured audience pattern.

    A pattern ending in ``*`` matches by prefix — ``hivegent-*`` accepts
    every client whose id starts with ``hivegent-``, so new clients need
    no config change — while any other pattern matches exactly.
    """
    for pattern in settings.auth.audience:
        if pattern.endswith("*"):
            if aud.startswith(pattern[:-1]):
                return True
        elif aud == pattern:
            return True
    return False


class _ClaimsRegistry(JWTClaimsRegistry):
    """JWT claims registry whose ``aud`` honours a trailing-``*`` prefix.

    joserfc's :class:`ClaimsOption` only matches audiences exactly, so the
    prefix case rides its per-claim ``validate_<name>`` hook — the same
    mechanism the library uses for ``exp``/``nbf`` — keeping audience
    inside the standard claims pipeline.  A mismatch raises the library's
    :class:`InvalidClaimError`, mapped to 401 like every other claim.  The
    claim is checked only when an audience is configured.
    """

    def validate_aud(self, value: Any) -> None:
        if not settings.auth.audience:
            return
        auds = value if isinstance(value, list) else [value]
        if not any(isinstance(a, str) and _audience_matches(a) for a in auds):
            raise InvalidClaimError("aud")


def _build_claims_registry() -> JWTClaimsRegistry:
    """Build a JWT claims registry based on current auth settings.

    Issuer comparison tolerates a trailing ``/`` on either side: Rauthy
    and several other IdPs emit ``iss`` with a trailing slash even when
    the configured URL doesn't, and OIDC mandates exact match. The
    expected value is normalized here; the actual claim is normalized in
    ``validate_jwt_token`` before validation.  ``aud`` is required and
    matched (:meth:`_ClaimsRegistry.validate_aud`) only when an audience
    is configured.
    """
    options: dict[str, Any] = {"sub": ClaimsOption(essential=True)}
    if settings.auth.issuer:
        options["iss"] = ClaimsOption(value=settings.auth.issuer.rstrip("/"))
    if settings.auth.audience:
        options["aud"] = ClaimsOption(essential=True)
    return _ClaimsRegistry(leeway=300, **options)


def _format_invalid_claim_detail(claim: str) -> str:
    """Build a 401 detail for an invalid claim.

    Echoes only the claim name — never the expected or received value —
    so an attacker can't probe the configured issuer/audience by sending
    crafted tokens.
    """
    return f"Invalid token claim: {claim!r}"


def parse_group_claim(claims: Mapping[str, Any]) -> Iterator[tuple[str, str | None]]:
    """Yield ``(group_id, permission)`` pairs from the OIDC groups claim.

    Each entry in the groups claim can be:
    - ``"engineering:write"`` -- permission ``"write"``
    - ``"sales:read"`` -- permission ``"read"``
    - ``"sales"`` -- bare name, permission ``None``

    Malformed entries (non-strings, empties, suffix-only) are skipped.
    """
    raw = claims.get(settings.claims.groups, [])
    if not isinstance(raw, list):
        return
    for entry in raw:
        if not isinstance(entry, str) or not entry:
            continue
        if ":" in entry:
            group_id, _, suffix = entry.rpartition(":")
            if group_id:
                yield group_id, suffix
        else:
            yield entry, None


def _extract_group_permissions(
    claims: dict[str, Any],
) -> tuple[frozenset[str], frozenset[str]]:
    """Split the groups claim into ``(read_groups, write_groups)``.

    Bare names without an explicit permission use the
    ``default_group_permission`` setting.
    """
    read_groups: set[str] = set()
    write_groups: set[str] = set()
    for group_id, permission in parse_group_claim(claims):
        write = permission == "write" or (
            permission is None and settings.claims.default_group_permission == "write"
        )
        (write_groups if write else read_groups).add(group_id)
    return frozenset(read_groups), frozenset(write_groups)


def _extract_roles(claims: Mapping[str, Any]) -> frozenset[str]:
    """Return the role names from the OIDC roles claim.

    Roles are global capabilities (e.g. admin), kept separate from the
    groups claim that models shared knowledge.  Non-string and empty
    entries are skipped.
    """
    raw = claims.get(settings.claims.roles, [])
    if not isinstance(raw, list):
        return frozenset()
    return frozenset(role for role in raw if isinstance(role, str) and role)


async def validate_jwt_token(token: str) -> User:
    """Validate a JWT token and extract user information.

    Args:
        token: The JWT token to validate.

    Returns:
        A User instance with extracted claims, groups, and admin status.

    Raises:
        HTTPException: If the token is invalid.
    """
    key_set = await _jwks_fetcher.get_jwks()
    algorithms = await _jwks_fetcher.get_allowed_algorithms()

    try:
        decoded = jwt.decode(token, key_set, algorithms=algorithms)
    except JoseError as e:
        if not _should_refresh_jwks(token, key_set):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid token signature",
                headers={"WWW-Authenticate": "Bearer"},
            ) from e

        try:
            key_set = await _jwks_fetcher.get_jwks(force_refresh=True)
            algorithms = await _jwks_fetcher.get_allowed_algorithms(force_refresh=True)
            decoded = jwt.decode(token, key_set, algorithms=algorithms)
        except JoseError as e:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid token signature",
                headers={"WWW-Authenticate": "Bearer"},
            ) from e

    try:
        claims_registry = _build_claims_registry()
        normalized_claims = dict(decoded.claims)
        token_iss = normalized_claims.get("iss")
        if isinstance(token_iss, str):
            normalized_claims["iss"] = token_iss.rstrip("/")
        claims_registry.validate(normalized_claims)
    except ExpiredTokenError as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token has expired",
            headers={"WWW-Authenticate": "Bearer"},
        ) from e
    except MissingClaimError as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Token missing required claim: {e.claim!r}",
            headers={"WWW-Authenticate": "Bearer"},
        ) from e
    except InvalidClaimError as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=_format_invalid_claim_detail(e.claim),
            headers={"WWW-Authenticate": "Bearer"},
        ) from e

    claims = decoded.claims
    sub = claims["sub"]
    try:
        sanitize_user_id(sub)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token subject is not a valid user identifier",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc

    read_groups, write_groups = _extract_group_permissions(claims)
    return User(
        id=sub,
        email=claims.get("email"),
        name=claims.get("name") or claims.get("preferred_username"),
        read_groups=read_groups,
        write_groups=write_groups,
        roles=_extract_roles(claims),
    )


# HTTP Bearer security scheme
security = HTTPBearer(auto_error=False)


async def _impersonate(actor: User, target_id: str) -> User:
    """Resolve the request identity to *target_id* on behalf of an admin.

    Stateless by construction: a user's groups and roles live only in
    their OIDC token, which we neither hold for the target nor persist.
    The impersonated session is therefore scoped to the target's
    personal workspace — enough to reproduce reported conversations and
    chunking — and carries no roles, so admin endpoints keep rejecting it
    and nested impersonation is impossible.
    """
    if not actor.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Administrator privileges required for impersonation",
        )
    try:
        target_id = sanitize_user_id(target_id)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid impersonation target",
        ) from exc
    if not await user_exists(target_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Unknown impersonation target",
        )
    logger.info("admin %r impersonating user %r", actor.id, target_id)
    return User(id=target_id)


async def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(security)],
    impersonate_user_id: Annotated[
        str | None, Header(alias=IMPERSONATE_HEADER)
    ] = None,
) -> User:
    """FastAPI dependency to get the current authenticated user.

    Validates the OIDC Bearer JWT from the Authorization header.  An
    admin may additionally send the ``X-Impersonate-User`` header to act
    as another user for troubleshooting; see :func:`_impersonate`.

    When HIVEGENT_AUTH__ENABLE=false, returns a dev user without validation.

    Args:
        credentials: The HTTP Bearer credentials.
        impersonate_user_id: Optional impersonation target (admins only).

    Returns:
        The authenticated User.

    Raises:
        HTTPException: If authentication fails.
    """
    if not settings.auth.enable:
        # Bypass authentication in development mode: give write access to
        # every group registered in the database and the admin role, so
        # destructive endpoints are reachable via the same
        # property-derived `is_admin` check used in prod.
        user = User(
            id="localhost",
            email="dev@localhost",
            name="Localhost User",
            write_groups=await list_group_ids(),
            roles=frozenset({ADMIN_ROLE}),
        )
    elif credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )
    else:
        user = await validate_jwt_token(credentials.credentials)

    if impersonate_user_id is None:
        return user
    return await _impersonate(user, impersonate_user_id)


async def require_admin(
    user: Annotated[User, Depends(get_current_user)],
) -> User:
    """FastAPI dependency that gates a route on administrator privileges.

    Admin status is the ``User.is_admin`` property — the fixed ``admin``
    role present in the request's ``roles`` claim.  PATs carry no roles,
    so admin actions must go through the OIDC flow.  Non-admins receive
    403.
    """
    if not user.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Administrator privileges required",
        )
    return user
