"""Purpose-separated keys derived from the application secret.

Nothing is stored: there is no key file to create, back up or lose, and a
second instance derives the same keys from the same configuration.
"""

from functools import cache

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from .config import MIN_SECRET_KEY_LENGTH, settings

__all__ = ["MissingSecretKey", "derived_ed25519_key", "derived_key"]

_SALT = b"hivegent"
_KEY_BYTES = 32


class MissingSecretKey(RuntimeError):
    """Raised when a derived key is requested without a usable secret."""


def derived_key(purpose: str, length: int = _KEY_BYTES) -> bytes:
    """Return the key for *purpose*, derived with HKDF-SHA256.

    Purpose separation is what keeps the keys independent: knowing one tells
    an attacker nothing about the others, and no consumer has to reuse a
    secret that belongs to something else.

    Raises:
        MissingSecretKey: If no usable application secret is configured.
    """
    secret = settings.secret_key

    if len(secret) < MIN_SECRET_KEY_LENGTH:
        raise MissingSecretKey(
            f"secret_key of at least {MIN_SECRET_KEY_LENGTH} characters is required"
        )

    return HKDF(
        algorithm=hashes.SHA256(),
        length=length,
        salt=_SALT,
        info=purpose.encode(),
    ).derive(secret.encode())


@cache
def derived_ed25519_key(purpose: str) -> Ed25519PrivateKey:
    """Return the Ed25519 private key for *purpose*.

    Cached because the key object is reused for every signature, and stable
    for as long as the application secret is.
    """
    return Ed25519PrivateKey.from_private_bytes(derived_key(purpose))
