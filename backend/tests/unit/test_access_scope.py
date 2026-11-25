"""The values that grant access are built only where access is checked."""

import ast
from collections.abc import Iterator
from pathlib import Path

import hivegent

_SRC = Path(hivegent.__file__).parent
_BUILDERS = {
    # The host operator wiping a user acts through `hivegent reset`, with no token.
    "User": {"auth.py", "cli.py"},
    # The host acts for itself in the CLI, at startup, in a source's sync job,
    # and for an admin's reindex, never for a request.
    "HostGrant": {
        "cli.py",
        "server/app.py",
        "server/routes/admin.py",
        "sources/sync.py",
    },
    # A run reaches what its deps name, a REST read what its user sees.
    "Reach": {"agents/common.py", "server/routes/cases.py"},
}


def _constructions() -> Iterator[tuple[str, str]]:
    """Each call of a guarded name, with its module."""
    for path in _SRC.rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(), path)):
            if not isinstance(node, ast.Call):
                continue

            match node.func:
                case ast.Name(id=name) | ast.Attribute(attr=name) if name in _BUILDERS:
                    yield name, path.relative_to(_SRC).as_posix()
                case _:
                    pass


def test_access_grants_have_known_builders() -> None:
    """A `User` comes only from authentication, the host's grant only from the host."""
    assert {
        (name, module)
        for name, module in _constructions()
        if module not in _BUILDERS[name]
    } == set()
