"""Small helpers shared across repositories.

Strictly cross-cutting utilities only — anything that touches a single
identity table lives in that table's own module (:mod:`.users`,
:mod:`.groups`, :mod:`.tokens`, …).
"""

from typing import Any

from sqlalchemy import or_

from .models import Document, _nid

__all__ = [
    "affected_rows",
    "new_id",
    "stem_subtree_filter",
]


def affected_rows(result: Any) -> int:
    return int(getattr(result, "rowcount", 0) or 0)


new_id = _nid


def stem_subtree_filter(prefix: str):
    """WHERE matching ``stem_path == prefix`` or ``stem_path LIKE 'prefix/%'``."""
    return or_(Document.stem_path == prefix, Document.stem_path.like(prefix + "/%"))
