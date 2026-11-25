"""Sanitizers for the ids and paths that name a workspace location."""

import re
from pathlib import PurePosixPath
from uuid import UUID

from cbrkit.io import is_partial

from ..shared.l10n import Localized
from ..shared.text import normalize_unicode

__all__ = [
    "parse_uuid",
    "sanitize_document_path",
    "sanitize_group_id",
    "sanitize_user_id",
]

# Only alphanumeric characters, underscores, and hyphens: an ID is a path
# segment, so anything else could traverse out of the workspace it names.
_SAFE_ID_PATTERN = re.compile(r"[a-zA-Z0-9_-]+")


_USER = Localized(en="User", de="Benutzer")

_GROUP = Localized(en="Group", de="Gruppen")


def _empty_id(kind: Localized[str]) -> Localized[str]:
    return Localized(
        en=f"{kind.en} ID cannot be empty", de=f"Die {kind.de}-ID darf nicht leer sein"
    )


def _invalid_id(kind: Localized[str], value: str) -> Localized[str]:
    return Localized(
        en=f"Invalid {kind.en.lower()} ID: {value!r}",
        de=f"Ungültige {kind.de}-ID: „{value}“",
    )


_EMPTY_PATH = Localized(
    en="Document path cannot be empty", de="Der Dokumentpfad darf nicht leer sein"
)

_NULL_BYTES = Localized(
    en="Document path contains null bytes", de="Der Dokumentpfad enthält Nullbytes"
)

_ABSOLUTE_PATH = Localized(
    en="Document path must be relative", de="Der Dokumentpfad muss relativ sein"
)


def _unsafe_segment(segment: str) -> Localized[str]:
    return Localized(
        en=f"Document path contains unsafe segment: {segment!r}",
        de=f"Der Dokumentpfad enthält das unsichere Segment „{segment}“",
    )


_EMPTY_SEGMENT = Localized(
    en="Document path contains empty segment",
    de="Der Dokumentpfad enthält ein leeres Segment",
)


def _sanitize_id(value: str, kind: Localized[str]) -> str:
    """Return *value* unchanged if it is safe as a path segment.

    Args:
        value: The identifier to check.
        kind: What the identifier names, for the error message.

    Returns:
        The identifier, unchanged.

    Raises:
        ValueError: If the identifier is empty or contains unsafe characters.
    """
    if not value:
        raise ValueError(_empty_id(kind).current)

    if not _SAFE_ID_PATTERN.fullmatch(value):
        raise ValueError(_invalid_id(kind, value).current)

    return value


def sanitize_user_id(user_id: str) -> str:
    """Sanitize a user ID to prevent path traversal attacks.

    Args:
        user_id: The user ID to sanitize.

    Returns:
        The sanitized user ID.

    Raises:
        ValueError: If the user ID is invalid or contains unsafe characters.
    """
    return _sanitize_id(user_id, _USER)


def sanitize_group_id(group_id: str) -> str:
    """Sanitize a group ID to prevent path traversal attacks.

    The ID is the identity provider's stable handle for the group. It names
    the workspace directory, keys the SQL rows, and forms the ``@<id>``
    prefix of every path addressing it, so it is held to the same narrow
    alphabet as a user ID.

    Args:
        group_id: The group ID to sanitize.

    Returns:
        The sanitized group ID.

    Raises:
        ValueError: If the group ID is invalid or contains unsafe characters.
    """
    return _sanitize_id(group_id, _GROUP)


def parse_uuid(value: str) -> UUID | None:
    """*value* as a UUID, or ``None`` when it is none.

    Every id the app or a client mints is a random UUID, and its canonical
    string, never the spelling it arrived in, is what names a folder on disk.

    >>> parse_uuid("0B6C4C9E-4F9A-4F0E-9D7A-3F6E2B1C8A90")
    UUID('0b6c4c9e-4f9a-4f0e-9d7a-3f6e2b1c8a90')
    >>> parse_uuid("../etc") is None
    True
    """
    try:
        return UUID(value)
    except ValueError:
        return None


def sanitize_document_path(path: str) -> str:
    """Sanitize a document path to prevent path traversal attacks.

    Normalizes the path to POSIX forward slashes and Unicode NFC, and rejects
    unsafe patterns.

    Args:
        path: The relative document path to sanitize.

    Returns:
        The sanitized POSIX-style relative path.

    Raises:
        ValueError: If the path is empty, absolute, or contains unsafe segments,
            the name of a partial file among them.
    """
    if not path:
        raise ValueError(_EMPTY_PATH.current)

    if "\x00" in path:
        raise ValueError(_NULL_BYTES.current)

    # Normalize to POSIX forward slashes and one canonical Unicode spelling
    normalized = str(PurePosixPath(normalize_unicode(path).replace("\\", "/")))

    if normalized.startswith("/"):
        raise ValueError(_ABSOLUTE_PATH.current)

    # Reject . and .. segments
    for segment in normalized.split("/"):
        if segment in (".", ".."):
            raise ValueError(_unsafe_segment(segment).current)
        if not segment:
            raise ValueError(_EMPTY_SEGMENT.current)

        # Reserved for interrupted writes, which the boot sweeps away.
        if is_partial(segment):
            raise ValueError(_unsafe_segment(segment).current)

    return normalized
