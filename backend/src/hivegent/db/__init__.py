"""Relational data layer (SQLAlchemy 2.0).

Source of truth for users, groups, tokens, memory, conversations,
documents, and chunks.  Workspace blobs stay on disk; chunk text and
vectors live together in the ``chunks`` table, cascading from
``documents`` on delete.

Submodules are imported lazily by callers via ``from .db import X`` to
avoid a cycle with :mod:`hivegent.types`, which both depends on this
package (for ``ConversationSummary``) and is depended on by it (for
``TokenInfo``).
"""

from .engine import resolve_database_url, session
from .migrations import apply_migrations, build_alembic_config
from .models import (
    Base,
    Chunk,
    Conversation,
    Document,
    EntryKind,
    GeneratedBy,
    Group,
    GroupMember,
    IndexState,
    Memory,
    Message,
    MessageKind,
    Origin,
    Permission,
    Timestamped,
    Token,
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
    "GroupMember",
    "IndexState",
    "Memory",
    "Message",
    "MessageKind",
    "Origin",
    "Permission",
    "Timestamped",
    "Token",
    "User",
    "apply_migrations",
    "build_alembic_config",
    "resolve_database_url",
    "session",
]
