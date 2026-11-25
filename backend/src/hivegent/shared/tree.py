"""Walking a directory tree."""

from collections.abc import Iterator
from pathlib import Path

__all__ = ["tree"]


def tree(path: Path) -> Iterator[Path]:
    """Yield *path* and everything below it."""
    yield path
    yield from path.rglob("*")
