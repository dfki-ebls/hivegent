"""Relational data layer (SQLAlchemy 2.0).

Source of truth for users, groups, tokens, memory, conversations,
documents, and chunks.  Workspace blobs stay on disk; LanceDB stays
as a derived index rebuildable from ``chunks``.

Submodules are imported lazily by callers via ``from .db import X`` to
avoid a cycle with :mod:`hivegent.types`, which both depends on this
package (for ``ConversationSummary``) and is depended on by it (for
``TokenInfo``).
"""

from .engine import Session, engine, init_database, session
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
    "Session",
    "Timestamped",
    "Token",
    "User",
    "engine",
    "init_database",
    "session",
]
