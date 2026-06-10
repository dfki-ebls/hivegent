"""Small helpers shared across repositories.

Strictly cross-cutting utilities only — anything that touches a single
identity table lives in that table's own module (:mod:`.users`,
:mod:`.groups`, …).
"""

from typing import Any

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from .models import Base, Document, _nid

__all__ = [
    "affected_rows",
    "ensure_row",
    "new_id",
    "stem_subtree_filter",
]


def affected_rows(result: Any) -> int:
    return int(getattr(result, "rowcount", 0) or 0)


new_id = _nid


async def ensure_row(s: AsyncSession, model: type[Base], /, **values: Any) -> None:
    """Idempotently insert an identity row, ignoring an existing primary key.

    Concurrency-safe ``INSERT ... ON CONFLICT DO NOTHING`` for rows that
    are materialised lazily on first reference: two requests racing to
    create the same row in separate transactions cannot trip the primary
    key and roll back the surrounding insert that triggered it.
    """
    await s.execute(pg_insert(model).values(**values).on_conflict_do_nothing())


def stem_subtree_filter(prefix: str):
    """WHERE matching stems strictly below *prefix* (``prefix/...``).

    Deliberately excludes ``stem_path == prefix`` itself: a stem equal to a
    directory path is the same-named *sibling document* (``notes.md`` next to
    ``notes/``), which must not be swept along when the directory moves or is
    deleted.  LIKE wildcards in the prefix are escaped so a stem containing
    ``%`` or ``_`` cannot widen the match.
    """
    escaped = prefix.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return Document.stem_path.like(escaped + "/%", escape="\\")
