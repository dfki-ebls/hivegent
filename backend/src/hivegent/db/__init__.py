"""Relational data layer (SQLAlchemy 2.0).

Source of truth for users, groups, memory, conversations, documents,
and chunks.  Workspace blobs stay on disk; chunk text and vectors live
together in the ``chunks`` table, cascading from ``documents`` on
delete.

Submodules are imported lazily by callers via ``from .db import X`` to
avoid a cycle with :mod:`hivegent.types`, which both depends on this
package (for ``ConversationSummary``) and is depended on by it.
"""

from .engine import engine_lifespan, resolve_database_url, session
from .migrations import apply_migrations, build_alembic_config
from .models import (
    Base,
    Chunk,
    Conversation,
    Document,
    EntryKind,
    GeneratedBy,
    Group,
    IndexState,
    Memory,
    Message,
    MessageKind,
    Origin,
    Timestamped,
    User,
)

__all__ = [
    "Base",
    "Chunk",
    "Conversation",
    "Document",
    "EntryKind",
    "GeneratedBy",
    "Group",
    "IndexState",
    "Memory",
    "Message",
    "MessageKind",
    "Origin",
    "Timestamped",
    "User",
    "apply_migrations",
    "build_alembic_config",
    "engine_lifespan",
    "resolve_database_url",
    "session",
]
