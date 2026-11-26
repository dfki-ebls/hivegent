"""Document filters, the documents a conversation hides from its operations."""

import glob
from dataclasses import dataclass, field
from itertools import chain
from pathlib import Path, PurePosixPath

from exar.memory.mounts import list_names
from pydantic import ConfigDict
from pydantic.dataclasses import dataclass as pydantic_dataclass

from ..tools.entries import Part, entry_owns, is_below

__all__ = [
    "DocumentFilter",
    "FilterEntry",
]


@pydantic_dataclass(frozen=True, slots=True, config=ConfigDict(extra="forbid"))
class FilterEntry:
    """A document or a directory a selection names, by its path.

    A request names it by its canonical path, a bare scope root as a directory
    being the whole workspace, and a :class:`DocumentFilter` by its local path,
    the empty one being the whole store.
    """

    path: str
    directory: bool


@dataclass(slots=True, frozen=True)
class DocumentFilter:
    """The documents a conversation hides from its document operations.

    A directory entry selects its subtree including the directory itself, the
    empty one the whole store, and a file entry selects the logical document:
    the file plus its same-stem siblings (description and original) and
    its ``.assets`` payload, mirroring how the inventory groups files into
    entries.

    Only the hidden half of the user's selection is enforced.  The documents
    the user points at are advisory and reach the model as prompt text
    (:func:`~hivegent.prompts.format_document_scope`), so naming the two files
    a question is about steers the run without cutting the rest of the
    workspace out from under it.  The model can still follow a reference out
    of them, and no write is refused for landing outside the selection.
    """

    excluded: frozenset[FilterEntry] = field(default_factory=frozenset)

    @staticmethod
    def _selects(entry: FilterEntry, path: str) -> bool:
        """Whether *entry* selects the file or directory *path*."""
        if entry.directory:
            return not entry.path or path == entry.path or is_below(path, entry.path)

        return path == entry.path or entry_owns(
            Part.of(entry.path).stem_path, Part.of(path).stem_path
        )

    def __call__(self, path: str) -> bool:
        """Return whether the file or directory *path* passes the filter."""
        return not any(self._selects(entry, path) for entry in self.excluded)

    def hides_within(self, path: str) -> bool:
        """Whether the filter hides *path* or anything below it, asked of its entries alone."""
        return any(
            is_below(entry.path, path) or self._selects(entry, path)
            for entry in self.excluded
        )

    def _hidden(self, entry: FilterEntry, root: Path) -> tuple[str, ...]:
        """The globs, anchored at the store, hiding every path below *root* that *entry* selects, a part added later too.

        A file entry hides every name its stem leads with a dot, then shows again
        the siblings present that the filter shows, which merely start like it,
        so a sibling added later stays hidden rather than a part of it showing.
        """
        if entry.directory:
            return _tree(entry.path)

        stem = PurePosixPath(Part.of(entry.path).stem_path)
        folder = stem.parent

        return (
            *_tree(stem.as_posix()),
            f"/{glob.escape(stem.as_posix())}.*",
            *(
                f"!/{glob.escape(path)}"
                for name in list_names(root / folder)
                if name.startswith(f"{stem.name}.")
                and self(path := (folder / name).as_posix())
            ),
        )

    def globs(self, root: Path) -> tuple[str, ...]:
        """The filter as the ``exclude`` globs of a :class:`~exar.memory.mounts.Mount` over *root*.

        An excluded entry re-includes only what the filter shows, so the order of
        the entries never matters.
        """
        return tuple(
            chain.from_iterable(self._hidden(entry, root) for entry in self.excluded)
        )


def _tree(path: str) -> tuple[str, ...]:
    """The globs, anchored at the store, of a path and everything below it, all of it for the empty one."""
    if not path:
        return ("**",)

    anchored = f"/{glob.escape(path)}"

    return (anchored, f"{anchored}/**")
