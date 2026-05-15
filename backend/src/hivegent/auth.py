"""OIDC authentication and JWT validation."""

import asyncio
import time
from typing import Annotated, Any

import httpx
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from fastmcp.server.auth.oidc_proxy import OIDCConfiguration
from joserfc import jwt
from joserfc.errors import (
    ExpiredTokenError,
    InvalidClaimError,
    JoseError,
    MissingClaimError,
)
from joserfc.jwk import KeySet
from joserfc.jwt import ClaimsOption, JWTClaimsRegistry
from pydantic import AnyHttpUrl, ValidationError

from .config import sanitize_user_id, settings
from .http_client import get_shared_http_client
from .tokens import token_store
from .types import User

__all__ = [
    "DEFAULT_JWT_ALGORITHMS",
    "User",
    "build_discovery_url",
    "fetch_oidc_configuration",
    "get_current_user",
]

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
    issuer: str, *, timeout_seconds: int = 10
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
    return OIDCConfiguration.get_oidc_configuration(
        config_url, strict=False, timeout_seconds=timeout_seconds
    )


class JWKSFetcher:
    """Fetch and cache JWKS from OIDC provider via discovery."""

    def __init__(self) -> None:
        self._cache: KeySet | None = None
        self._cache_time: float = 0
        self._discovery_cache: OIDCConfiguration | None = None
        self._discovery_cache_time: float = 0

    def _is_fresh(self, cached: object, cached_time: float) -> bool:
        return (
            cached is not None
            and (time.time() - cached_time) < settings.auth.jwks_cache_ttl
        )

    async def _get_discovery(self, force_refresh: bool = False) -> OIDCConfiguration:
        """Fetch the OIDC discovery document.

        Raises:
            HTTPException: 503 on transport failure, 500 on malformed payload
                or missing configuration.
        """
        if not force_refresh and self._is_fresh(
            self._discovery_cache, self._discovery_cache_time
        ):
            assert self._discovery_cache is not None
            return self._discovery_cache

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

        self._discovery_cache = config
        self._discovery_cache_time = time.time()
        return config

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

    async def get_jwks(self, force_refresh: bool = False) -> KeySet:
        """Fetch JWKS from the OIDC provider, resolving the URI via discovery.

        Raises:
            HTTPException: If JWKS cannot be fetched.
        """
        if not force_refresh and self._is_fresh(self._cache, self._cache_time):
            assert self._cache is not None
            return self._cache

        config = await self._get_discovery(force_refresh=force_refresh)
        jwks_uri = str(config.jwks_uri)

        try:
            response = await get_shared_http_client().get(jwks_uri, timeout=10.0)
            response.raise_for_status()
            jwks_data = response.json()
        except httpx.HTTPError as e:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=f"Failed to fetch JWKS: {e}",
            ) from e

        try:
            self._cache = KeySet.import_key_set(jwks_data)
            self._cache_time = time.time()
            return self._cache
        except Exception as e:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Invalid JWKS format: {e}",
            ) from e


_jwks_fetcher = JWKSFetcher()


def _build_claims_registry() -> JWTClaimsRegistry:
    """Build a JWT claims registry based on current auth settings.

    Issuer comparison tolerates a trailing ``/`` on either side: Rauthy
    and several other IdPs emit ``iss`` with a trailing slash even when
    the configured URL doesn't, and OIDC mandates exact match. The
    expected value is normalized here; the actual claim is normalized in
    ``validate_jwt_token`` before validation.
    """
    options: dict[str, Any] = {"sub": ClaimsOption(essential=True)}
    if settings.auth.issuer:
        options["iss"] = ClaimsOption(value=settings.auth.issuer.rstrip("/"))
    if settings.auth.audience:
        options["aud"] = ClaimsOption(value=settings.auth.audience)
    return JWTClaimsRegistry(leeway=300, **options)


def _format_invalid_claim_detail(claim: str) -> str:
    """Build a 401 detail for an invalid claim.

    Echoes only the claim name — never the expected or received value —
    so an attacker can't probe the configured issuer/audience by sending
    crafted tokens.
    """
    return f"Invalid token claim: {claim!r}"


def _extract_group_permissions(
    claims: dict[str, Any],
) -> tuple[frozenset[str], frozenset[str]]:
    """Extract group memberships and permissions from JWT claims.

    Each entry in the groups claim can be:
    - ``"engineering:write"`` -- write (and read) access
    - ``"sales:read"`` -- explicit read-only access
    - ``"sales"`` -- bare name, uses ``default_permission`` setting

    Args:
        claims: The decoded JWT claims dictionary.

    Returns:
        Tuple of ``(read_groups, write_groups)``.
    """
    raw = claims.get(settings.groups.groups_claim, [])
    if not isinstance(raw, list):
        return frozenset(), frozenset()

    read_groups: set[str] = set()
    write_groups: set[str] = set()

    for entry in raw:
        if not isinstance(entry, str) or not entry:
            continue
        if ":" in entry:
            group_id, _, suffix = entry.rpartition(":")
            if not group_id:
                continue
            if suffix == "write":
                write_groups.add(group_id)
            else:
                read_groups.add(group_id)
        else:
            if settings.groups.default_permission == "write":
                write_groups.add(entry)
            else:
                read_groups.add(entry)

    return frozenset(read_groups), frozenset(write_groups)


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
    except JoseError:
        # Try refreshing JWKS in case keys were rotated
        try:
            key_set = await _jwks_fetcher.get_jwks(force_refresh=True)
            algorithms = await _jwks_fetcher.get_allowed_algorithms(force_refresh=True)
            decoded = jwt.decode(token, key_set, algorithms=algorithms)
        except JoseError as e:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=f"Invalid token signature: {e}",
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
    )


# HTTP Bearer security scheme
security = HTTPBearer(auto_error=False)


async def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(security)],
) -> User:
    """FastAPI dependency to get the current authenticated user.

    Validates the Bearer token from the Authorization header.
    Supports both JWT tokens and Personal Access Tokens (PATs).

    When HIVEGENT_AUTH__ENABLE=false, returns a dev user without validation.

    Args:
        credentials: The HTTP Bearer credentials.

    Returns:
        The authenticated User.

    Raises:
        HTTPException: If authentication fails.
    """
    # Bypass authentication in development mode
    if not settings.auth.enable:
        # Give write access to all groups that exist on disk
        groups_dir = settings.data_dir / "groups"
        dev_groups = (
            frozenset(d.name for d in groups_dir.iterdir() if d.is_dir())
            if groups_dir.exists()
            else frozenset[str]()
        )
        return User(
            id="localhost",
            email="dev@localhost",
            name="Localhost User",
            write_groups=dev_groups,
        )

    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )

    token = credentials.credentials

    if token.startswith("hivegent_"):
        # Argon2 is CPU-bound (~10ms); run off the event loop so concurrent
        # requests aren't blocked during PAT verification.
        user = await asyncio.to_thread(token_store.validate_token, token)
        if user:
            return user
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid personal access token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return await validate_jwt_token(token)
