"""Every outbound client is minted by the egress modules, enforced on the AST.

A client built elsewhere could connect directly, honour proxy variables, or
reach a unix socket, so only ``http_client`` and ``security`` construct one,
and only the settings mint the ``OperatorUrl`` that opens a direct one.
"""

import ast
from pathlib import Path

import hivegent

ROOT = Path(hivegent.__file__).parent

_HTTPX = frozenset({"httpx", "httpx2"})
_CLIENTS = frozenset({"Client", "AsyncClient", "HTTPTransport", "AsyncHTTPTransport"})


def _calls(names: frozenset[str], allowed: frozenset[str]) -> list[str]:
    """The calls of *names*, plain or of an httpx module, outside the *allowed* files."""
    found: list[str] = []

    for path in sorted(ROOT.rglob("*.py")):
        if path.relative_to(ROOT).as_posix() in allowed:
            continue

        for node in ast.walk(ast.parse(path.read_text(), str(path))):
            match node:
                case ast.Call(
                    func=ast.Attribute(value=ast.Name(id=module), attr=name)
                ) if module in _HTTPX and name in names:
                    pass
                case ast.Call(func=ast.Name(id=name)) if name in names:
                    pass
                case ast.ImportFrom(module=module, names=aliases) if (
                    module in _HTTPX and any(alias.name in names for alias in aliases)
                ):
                    pass
                case _:
                    continue

            found.append(f"{path.relative_to(ROOT)}:{node.lineno}")

    return found


def test_only_the_egress_modules_build_http_clients() -> None:
    assert _calls(_CLIENTS, frozenset({"http_client.py", "security.py"})) == []


def test_only_the_settings_mint_operator_urls() -> None:
    assert (
        _calls(
            frozenset({"OperatorUrl"}),
            frozenset(
                {
                    "config/common.py",
                    "config/llm.py",
                    "config/security.py",
                    "security.py",
                }
            ),
        )
        == []
    )
