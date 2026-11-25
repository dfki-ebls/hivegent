"""The self-contained packages import nothing of the app, enforced on the AST.

``tools`` may import itself and ``shared`` alone, and ``shared``, which holds the
pipeline contract, imports only itself, so each ships with nothing of the app
but ``shared``.  Ingestion itself comes from ``exar.ingest``, which the check
leaves alone as any library.  The plugin facade is held to the same, so a
plugin depends on no app internals.
"""

import ast
from collections.abc import Iterator
from pathlib import Path

import pytest

import hivegent

ROOT = Path(hivegent.__file__).parent

SELF_CONTAINED = frozenset({"tools"})
"""The packages AGENTS.md holds self-contained."""


def _files(name: str) -> list[Path]:
    """The source files of the top-level module or package *name*."""
    path = ROOT / name

    return sorted(path.rglob("*.py")) if path.is_dir() else [path.with_suffix(".py")]


def _imports(path: Path) -> Iterator[tuple[int, tuple[str, ...]]]:
    """Every hivegent module *path* imports, with its line, relative ones resolved."""
    module = path.relative_to(ROOT.parent).with_suffix("").parts
    package = module[:-1]

    for node in ast.walk(ast.parse(path.read_text(), str(path))):
        match node:
            case ast.Import(names=names):
                targets = [tuple(alias.name.split(".")) for alias in names]
            case ast.ImportFrom(module=name, level=level, names=names):
                base = package[: len(package) - level + 1] if level else ()
                target = (*base, *(name.split(".") if name else ()))
                # ``from . import x`` may name a submodule, so each counts.
                targets = [target] if name else [(*target, a.name) for a in names]
            case _:
                continue

        for target in targets:
            if target[0] == "hivegent":
                yield node.lineno, target


def violations(name: str, allowed: frozenset[str]) -> list[str]:
    """The imports of the module or package *name* reaching beyond *allowed*."""
    return [
        f"{path.relative_to(ROOT)}:{line} imports {'.'.join(target)}"
        for path in _files(name)
        for line, target in _imports(path)
        if len(target) < 2 or target[1] not in allowed
    ]


@pytest.mark.parametrize("package", sorted(SELF_CONTAINED | {"shared"}))
def test_packages_import_only_themselves_and_shared(package: str) -> None:
    assert violations(package, frozenset({package, "shared"})) == []


@pytest.mark.parametrize("module", ["plugins/__init__", "plugins/api"])
def test_the_plugin_facade_leaks_no_app_internals(module: str) -> None:
    """A plugin sees the contracts of the self-contained packages alone."""
    assert violations(module, SELF_CONTAINED | {"shared", "plugins"}) == []


@pytest.mark.parametrize("module", ["sources/webdav", "sources/local"])
def test_a_built_in_connector_uses_only_the_plugin_api(module: str) -> None:
    """A built-in connector is written as a plugin could write it."""
    assert violations(module, frozenset({"plugins", "shared"})) == []
