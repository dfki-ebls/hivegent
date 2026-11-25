"""Hashing and normalizing the text of workspace paths and documents."""

import hashlib
import unicodedata

from exar.text import truncate_line

__all__ = [
    "content_hash",
    "failure_text",
    "normalize_unicode",
    "sha256",
]


def sha256(text: str) -> str:
    """Return the SHA-256 hex digest of *text*'s UTF-8 bytes.

    Hivegent's own, since it is stored and published, such as in staged
    changesets and signed export provenance, so no library update changes it.

    >>> sha256("hello world")[:16]
    'b94d27b9934d3e08'
    """
    return hashlib.sha256(text.encode()).hexdigest()


def content_hash(text: str) -> str:
    """Return the first 12 hex characters of *text*'s SHA-256 digest.

    A read surfaces it, so a later write passes it back as ``expected_hash``.

    >>> content_hash("hello world")
    'b94d27b9934d'
    """
    return sha256(text)[:12]


def failure_text(exc: BaseException) -> str:
    """Return *exc* as one line of at most 200 characters, for a status a person reads.

    >>> failure_text(ValueError("bad"))
    'ValueError: bad'
    """
    return truncate_line(f"{type(exc).__name__}: {exc}", 200)


def normalize_unicode(value: str) -> str:
    """Return the path *value* in Unicode NFC, the canonical spelling for every path.

    NFC since it is lossless, unlike NFKC, and never applied to file content,
    whose digests and ``expected_hash`` tokens it would invalidate.

    >>> normalize_unicode("SÜVOA.md")
    'SÜVOA.md'
    >>> normalize_unicode("ﬁle.md")
    'ﬁle.md'
    """
    return unicodedata.normalize("NFC", value)
