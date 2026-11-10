"""The values that grant access are built only where access is checked."""

import ast
from collections.abc import Iterator
from pathlib import Path
from uuid import uuid4

import pytest

import hivegent
from hivegent.db.conversations import any_conversation
from hivegent.store import AccessDenied, User

_SRC = Path(hivegent.__file__).parent
_BUILDERS = {
    "OwnedConversation": {"db/conversations.py"},
    "User": {"auth.py"},
    # A request derives its stores from its `User`, so only system code that
    # states a grant of its own, or names an owner for a read, builds one.
    "Casebase": {
        "store.py",
        "db/documents.py",
        "reconcile.py",
        "retrieval.py",
        "server/routes/admin.py",
    },
}
_CONSTRUCTORS = {"for_user", "for_group", "for_owner", "from_store_key"}


def _constructions() -> Iterator[tuple[str, str]]:
    """Each call of a guarded class or one of its constructors, with its module."""
    for path in _SRC.rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(), path)):
            if not isinstance(node, ast.Call):
                continue

            func = node.func
            target = (
                func.value
                if isinstance(func, ast.Attribute) and func.attr in _CONSTRUCTORS
                else func
            )

            if isinstance(target, ast.Name) and target.id in _BUILDERS:
                yield target.id, path.relative_to(_SRC).as_posix()


def test_access_grants_have_known_builders() -> None:
    """A `User` comes only from authentication, the proofs and grants only from their checks."""
    assert {
        (name, module)
        for name, module in _constructions()
        if module not in _BUILDERS[name]
    } == set()


async def test_only_an_admin_reads_any_conversation() -> None:
    """The cross-user lookup refuses a caller without the admin role before it reads."""
    with pytest.raises(AccessDenied):
        _ = await any_conversation(User("u"), uuid4())
