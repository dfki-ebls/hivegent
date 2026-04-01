"""OIDC authentication and JWT validation."""

import time
from typing import Annotated, Any

import httpx
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from joserfc import jwt
from joserfc.errors import (
    ExpiredTokenError,
    InvalidClaimError,
    JoseError,
    MissingClaimError,
)
from joserfc.jwk import KeySet
from joserfc.jwt import ClaimsOption, JWTClaimsRegistry
from pydantic_settings import BaseSettings, SettingsConfigDict

from .config import settings
from .tokens import token_store
from .types import User

__all__ = [
    "AuthSettings",
    "User",
    "get_current_user",
]


class AuthSettings(BaseSettings):
    """Authentication settings loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_prefix="HIVEGENT_AUTH_",
        env_file=".env",
        env_file_encoding="utf-8",
    )

    disabled: bool = False
    issuer: str = ""
    audience: str | None = None
    jwks_cache_ttl: int = 3600


auth_settings = AuthSettings()


class JWKSFetcher:
    """Fetch and cache JWKS from OIDC provider."""

    def __init__(self) -> None:
        self._cache: KeySet | None = None
        self._cache_time: float = 0
        self._client: httpx.AsyncClient | None = None

    def _get_client(self) -> httpx.AsyncClient:
        """Lazily create the HTTP client on first use."""
        if self._client is None:
            self._client = httpx.AsyncClient()
        return self._client

    def _is_cache_valid(self) -> bool:
        """Check if the cached JWKS is still valid."""
        if self._cache is None:
            return False
        return (time.time() - self._cache_time) < auth_settings.jwks_cache_ttl

    async def get_jwks(self, force_refresh: bool = False) -> KeySet:
        """Fetch JWKS from the OIDC provider.

        Args:
            force_refresh: Force a refresh even if cache is valid.

        Returns:
            A KeySet containing the provider's public keys.

        Raises:
            HTTPException: If JWKS cannot be fetched.
        """
        if not force_refresh and self._is_cache_valid():
            assert self._cache is not None
            return self._cache

        if not auth_settings.issuer:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="OIDC issuer not configured",
            )

        jwks_uri = f"{auth_settings.issuer.rstrip('/')}/.well-known/jwks.json"

        try:
            response = await self._get_client().get(jwks_uri, timeout=10.0)
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

    Returns:
        A configured JWTClaimsRegistry instance.
    """
    options: dict[str, Any] = {"sub": ClaimsOption(essential=True)}
    if auth_settings.issuer:
        options["iss"] = ClaimsOption(value=auth_settings.issuer)
    if auth_settings.audience:
        options["aud"] = ClaimsOption(value=auth_settings.audience)
    return JWTClaimsRegistry(leeway=300, **options)


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

    try:
        decoded = jwt.decode(token, key_set)
    except JoseError:
        # Try refreshing JWKS in case keys were rotated
        try:
            key_set = await _jwks_fetcher.get_jwks(force_refresh=True)
            decoded = jwt.decode(token, key_set)
        except JoseError as e:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=f"Invalid token signature: {e}",
                headers={"WWW-Authenticate": "Bearer"},
            ) from e

    try:
        claims_registry = _build_claims_registry()
        claims_registry.validate(decoded.claims)
    except ExpiredTokenError as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token has expired",
            headers={"WWW-Authenticate": "Bearer"},
        ) from e
    except MissingClaimError as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Token missing required claim: {e}",
            headers={"WWW-Authenticate": "Bearer"},
        ) from e
    except InvalidClaimError as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Invalid token claim: {e}",
            headers={"WWW-Authenticate": "Bearer"},
        ) from e

    claims = decoded.claims
    read_groups, write_groups = _extract_group_permissions(claims)
    return User(
        id=claims["sub"],
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

    When HIVEGENT_AUTH_DISABLED=true, returns a dev user without validation.

    Args:
        credentials: The HTTP Bearer credentials.

    Returns:
        The authenticated User.

    Raises:
        HTTPException: If authentication fails.
    """
    # Bypass authentication in development mode
    if auth_settings.disabled:
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

    # Check if this is a Personal Access Token (starts with hivegent_)
    if token.startswith("hivegent_"):
        user = token_store.validate_token(token)
        if user:
            return user
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid personal access token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Otherwise validate as JWT
    return await validate_jwt_token(token)
