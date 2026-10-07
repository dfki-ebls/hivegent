"""Helpers for logical stem-based workspace entries."""

import os
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Self

from .config import normalize_unicode
from .converters import projects_verbatim
from .converters.base import DOCUMENT_EXTENSION, is_markdown_suffix

__all__ = [
    "ContentStat",
    "EntryPaths",
    "Listdir",
    "asset_ref_for",
    "assets_dir_for_stem",
    "description_path_for_stem",
    "entry_exists",
    "entry_owns",
    "find_original_for_stem",
    "folds_case",
    "is_assets_dir",
    "is_below",
    "is_description_file",
    "is_ignorable_path",
    "is_inside_assets_dir",
    "is_projectable_original",
    "is_reserved_path",
    "list_names",
    "original_path_for_stem",
    "path_key",
    "rebase",
    "repoint_asset_refs",
    "resolve_entry_paths",
    "respell",
    "stem_path_from_reference",
]


@dataclass(slots=True, frozen=True)
class ContentStat:
    """A file's ``(mtime_ns, size, inode)`` fingerprint.

    Lets the reconciler skip reading and hashing a description whose stat is
    unchanged since it was last indexed, and lets a change that never decoded a
    file refuse one that moved on since it was seen.  The content digest stays
    the authority: a stat mismatch only triggers a read + hash, and a re-embed
    happens solely when the digest itself differs, so a stat that lies in the
    "changed" direction (a ``touch``, checkout, or restore) costs one read, not
    a re-embed.

    The inode is what catches the one lie in the other direction: a file
    replaced by another of the same size whose mtime was preserved (``cp -p``,
    ``rsync -t``, an editor saving through a rename within one mtime tick) is a
    new inode, while every in-place write and rename keeps it.
    """

    mtime_ns: int
    size: int
    inode: int

    @classmethod
    def of(cls, st: os.stat_result) -> Self:
        """Return the fingerprint of an existing stat."""
        return cls(mtime_ns=st.st_mtime_ns, size=st.st_size, inode=st.st_ino)

    @classmethod
    def from_path(cls, path: Path) -> Self | None:
        """Return the stat fingerprint of *path*, or ``None`` if it is unreadable."""
        try:
            return cls.of(path.stat())
        except OSError:
            return None


_FOLDING: dict[int, bool] = {}
"""Whether each filesystem folds case, by device, which is as many as are mounted."""


def _probe_folding(directory: Path) -> bool:
    for candidate in (directory, *directory.parents):
        swapped = candidate.with_name(candidate.name.swapcase()) if candidate.name else candidate

        if swapped == candidate:
            continue

        try:
            return swapped.samefile(candidate)
        except OSError:
            return False

    return False


def folds_case(directory: Path) -> bool:
    """Whether the filesystem holding *directory* treats names differing in case as one.

    Probed once per device, read-only: the nearest existing ancestor whose
    name has a case is looked up under its swapped spelling, which a
    case-insensitive filesystem (macOS, Windows) resolves to the same inode.
    Every later call costs one ``stat``, however many directories ask.
    """
    for candidate in (directory, *directory.parents):
        try:
            device = candidate.stat().st_dev
        except OSError:
            continue

        folded = _FOLDING.get(device)

        if folded is None:
            folded = _FOLDING[device] = _probe_folding(candidate)

        return folded

    return False


def path_key(path: str, *, folded: bool) -> str:
    """The identity two spellings of *path* share, for comparing them.

    NFC like every inbound path, and case-folded on a filesystem that
    :func:`folds_case`, where ``A.md`` and ``a.md`` are one file.

    >>> path_key("Ä/B.md", folded=True) == path_key("ä/b.md", folded=True)
    True
    >>> path_key("A.md", folded=False) == path_key("a.md", folded=False)
    False
    """
    path = normalize_unicode(path)

    return path.casefold() if folded else path


type Listdir = Callable[[Path], Sequence[str]]
"""Lists a directory's names, which :func:`respell` takes so a caller can cache them."""


def list_names(directory: Path) -> Sequence[str]:
    """The names in *directory*, none when it cannot be listed."""
    try:
        return os.listdir(directory)
    except OSError:
        return ()


def respell(root: Path, local: str, listdir: Listdir | None = None) -> str:
    """*local* under *root*, each existing segment spelled the way the disk spells it.

    On a case-insensitive filesystem ``A.md`` opens ``a.md``, so a path a caller
    spelled differently would otherwise become a second name for one file: a
    second row for one entry, a filter asked about a name the file does not
    have.  A missing segment keeps its spelling, and so does every path on a
    case-sensitive filesystem.  *listdir* lets a caller cache the listings.
    """
    if not local or not folds_case(root):
        return local

    listdir = listdir or list_names
    spelled = PurePosixPath()

    for part in PurePosixPath(local).parts:
        key = path_key(part, folded=True)
        spelled /= next(
            (name for name in listdir(root / spelled) if path_key(name, folded=True) == key),
            part,
        )

    return str(spelled)


@dataclass(slots=True, frozen=True)
class EntryPaths:
    """Resolved paths for a logical stem entry."""

    stem_path: str
    description_path: str
    original_path: str | None
    assets_dir: str | None

    @property
    def files(self) -> tuple[str, ...]:
        """The description, original, and assets directory the entry has, in that order."""
        return tuple(
            path
            for path in (self.description_path, self.original_path, self.assets_dir)
            if path is not None
        )

    def at(self, stem_path: str) -> Self:
        """The entry relocated to *stem_path*, every companion it has renamed along.

        >>> EntryPaths("a.tar", "a.tar.md", "a.tar.gz", None).at("b/c")
        EntryPaths(stem_path='b/c', description_path='b/c.md', original_path='b/c.gz', assets_dir=None)
        """
        suffix = None if self.original_path is None else self.original_path[len(self.stem_path) :]

        return type(self)(
            stem_path=stem_path,
            description_path=description_path_for_stem(stem_path),
            original_path=original_path_for_stem(stem_path, suffix),
            assets_dir=assets_dir_for_stem(stem_path) if self.assets_dir else None,
        )


def stem_path_from_reference(reference: str) -> str:
    """Return the logical stem path for a workspace-relative reference."""
    pure = PurePosixPath(reference)
    if pure.suffix:
        return str((pure.parent / pure.stem).as_posix())
    return str(pure.as_posix())


def is_below(path: str, directory: str) -> bool:
    """Whether *path* lies strictly inside *directory*, compared by segments.

    Both are normalized POSIX paths, local or canonical, so a sibling sharing
    a name prefix is never mistaken for a child.

    >>> is_below("~/docs/a.md", "~/docs")
    True
    >>> is_below("~/docs-old/a.md", "~/docs")
    False
    >>> is_below("~/docs", "~/docs")
    False
    """
    return path != directory and PurePosixPath(path).is_relative_to(directory)


def rebase(path: str, old: str, new: str) -> str:
    """Carry *path*, which is *old* or lies below it, to the same place under *new*.

    >>> rebase("~/a/b/c.md", "~/a", "@team/x")
    '@team/x/b/c.md'
    >>> rebase("~/a", "~/a", "~/b")
    '~/b'
    """
    return str(PurePosixPath(new, PurePosixPath(path).relative_to(old)))


def is_assets_dir(name: str) -> bool:
    """Return whether a directory name is a child-assets directory."""
    return name.endswith(".assets")


def is_reserved_path(path: str) -> bool:
    """Whether *path* is or reaches into an ``.assets`` payload.

    Such a payload belongs to its document entry and is hidden from the tree,
    so content landed there by a generic create, move, or upload would be
    silently disowned.

    >>> is_reserved_path("notes/report.assets/fig1.png")
    True
    >>> is_reserved_path("notes/report.md")
    False
    """
    return any(is_assets_dir(part) for part in PurePosixPath(path).parts)


_JUNK_FILENAMES = frozenset({".DS_Store", "Thumbs.db", "ehthumbs.db", "desktop.ini"})
_JUNK_DIRECTORIES = frozenset({"__MACOSX"})


def is_ignorable_path(rel_path: str) -> bool:
    """Return whether a path is OS-generated junk that must never be indexed.

    Directory uploads and ZIP archives routinely carry Finder/Explorer metadata
    (``.DS_Store``, ``Thumbs.db``, ``desktop.ini``), AppleDouble resource forks
    (``._name``), and the macOS ``__MACOSX`` sidecar folder.  None of these is
    user content, so they are dropped before planning rather than reaching the
    converter and failing as an unsupported binary.

    >>> is_ignorable_path("docs/report.pdf")
    False
    >>> is_ignorable_path("docs/.DS_Store")
    True
    >>> is_ignorable_path("__MACOSX/docs/._report.pdf")
    True
    """
    pure = PurePosixPath(rel_path)
    if any(part in _JUNK_DIRECTORIES for part in pure.parts):
        return True

    return pure.name in _JUNK_FILENAMES or pure.name.startswith("._")


def is_description_file(rel_path: str) -> bool:
    """Return whether a workspace-relative path is an ingestable description.

    The content-versus-document policy seam: only markdown files map to
    logical document entries that the reconciler folds into SQL.  Every
    other on-disk file (originals, store-only assets, and any output a
    future shell tool produces) is inert workspace content that is kept on
    disk but never chunked on its own.

    >>> is_description_file("docs/report.md")
    True
    >>> is_description_file("docs/report.pdf")
    False
    """
    return is_markdown_suffix(PurePosixPath(rel_path).suffix)


def is_inside_assets_dir(rel_path: str) -> bool:
    """Return whether a path lies within a managed ``.assets`` payload.

    The directory itself is not inside one, so an entry's own assets directory
    is addressable while everything it holds is owned by that entry.

    >>> is_inside_assets_dir("docs/report.assets/fig1.png")
    True
    >>> is_inside_assets_dir("docs/report.assets")
    False
    """
    return any(is_assets_dir(part) for part in PurePosixPath(rel_path).parts[:-1])


def is_projectable_original(rel_path: str) -> bool:
    """Return whether the ingest pass may derive a description for this file.

    The counterpart of :func:`is_description_file`: a file whose projection is
    a verbatim copy of its own text (:func:`~hivegent.converters.projects_verbatim`)
    and that is an entry of its own — so neither OS junk nor a managed asset.
    Everything else waits for an upload or a reconvert, which is where a
    converter or a vision model can be afforded.

    >>> is_projectable_original("docs/settings.ini")
    True
    >>> is_projectable_original("docs/report.md")
    False
    >>> is_projectable_original("docs/photo.png")
    False
    >>> is_projectable_original("docs/report.assets/notes.txt")
    False
    """
    return (
        not is_ignorable_path(rel_path)
        and not is_inside_assets_dir(rel_path)
        and projects_verbatim(PurePosixPath(rel_path).name)
    )


def description_path_for_stem(stem_path: str) -> str:
    """Return the markdown description path for a logical stem."""
    return f"{stem_path}{DOCUMENT_EXTENSION}"


def assets_dir_for_stem(stem_path: str) -> str:
    """Return the child-assets directory path for a logical stem."""
    return f"{stem_path}.assets"


def entry_owns(stem_path: str, path: str) -> bool:
    """Whether the logical entry at *stem_path* owns the file or directory *path*.

    An entry owns its own stem plus everything in its ``.assets`` payload,
    including the directory itself.  *path* must already be a stem or a
    directory path (see :func:`stem_path_from_reference`), never a raw file
    reference, since a stem may itself contain dots.

    >>> entry_owns("docs/report", "docs/report.assets/fig1")
    True
    >>> entry_owns("docs/report", "docs/reports")
    False
    """
    assets_dir = assets_dir_for_stem(stem_path)

    return path in (stem_path, assets_dir) or is_below(path, assets_dir)


def asset_ref_for(assets_dir: str, relpath: str) -> str:
    """Return the in-markdown reference for an extracted asset.

    Assets are referenced relative to the description's sibling ``.assets``
    directory as ``<assets-dir-basename>/<relpath>``, the form that round-trips
    with the reference rewriting in
    :func:`workspace.prepare._replace_image_references`.

    >>> asset_ref_for("docs/report.assets", "img/fig1.png")
    'report.assets/img/fig1.png'
    """
    return str(PurePosixPath(PurePosixPath(assets_dir).name) / relpath)


def repoint_asset_refs(markdown: str, src_name: str, dst_name: str) -> str:
    """Repoint ``<name>.assets/`` references after an entry's basename changed.

    A description addresses its payload by basename (see :func:`asset_ref_for`),
    so a rename that changes the basename leaves every reference pointing at a
    directory that no longer exists.  Every renamer needs the same rewrite, and
    the reference format is the thing that must not drift between them.

    >>> repoint_asset_refs("![](old.assets/fig.png)", "old", "new")
    '![](new.assets/fig.png)'
    """
    return markdown.replace(f"{src_name}.assets/", f"{dst_name}.assets/")


def original_path_for_stem(stem_path: str, original_suffix: str | None) -> str | None:
    """Return the original-file path for a stem, or ``None`` when there is none.

    *original_suffix* is the original file's pathlib suffix including its
    leading dot.  ``None`` means there is no original; an empty string means the
    original has no extension and so its path is the bare stem — the case for an
    extension-less upload (``abc``) or a dotfile (``.env``), both of which
    pathlib reports as having no suffix.

    >>> original_path_for_stem("docs/report", ".pdf")
    'docs/report.pdf'
    >>> original_path_for_stem("docs/data", "")
    'docs/data'
    >>> original_path_for_stem("docs/note", None) is None
    True
    """
    return f"{stem_path}{original_suffix}" if original_suffix is not None else None


def find_original_for_stem(
    workspace_dir: Path, stem_path: str, listdir: Listdir = list_names
) -> str | None:
    """Return the workspace-relative original path for a logical stem.

    Takes a raw stem path, not a reference: a stem may itself contain dots
    (``a.tar`` from ``a.tar.gz``), so re-deriving it here via
    :func:`stem_path_from_reference` would strip part of the name and miss
    the entry's sibling files.

    The two name tests run before ``is_file``, which is the only predicate
    that costs a syscall: they reject every entry but the handful sharing the
    stem, so the scan is one directory listing rather than one ``stat`` per
    file in it.  *listdir* lets a caller resolving many stems of one folder
    list it once.
    """
    stem_pure = PurePosixPath(stem_path)
    parent = stem_pure.parent
    description = f"{stem_pure.name}{DOCUMENT_EXTENSION}"
    candidates = sorted(
        name
        for name in listdir(workspace_dir / parent)
        if name != description
        and PurePosixPath(name).stem == stem_pure.name
        and (workspace_dir / parent / name).is_file()
    )
    if not candidates:
        return None

    return str(parent / candidates[0])


def resolve_entry_paths(
    workspace_dir: Path, reference: str, listdir: Listdir = list_names
) -> EntryPaths:
    """Resolve logical entry paths from any workspace-relative reference."""
    stem_path = stem_path_from_reference(reference)
    assets_dir = assets_dir_for_stem(stem_path)
    assets_full = workspace_dir / assets_dir
    return EntryPaths(
        stem_path=stem_path,
        description_path=description_path_for_stem(stem_path),
        original_path=find_original_for_stem(workspace_dir, stem_path, listdir),
        assets_dir=assets_dir
        if assets_full.exists() and assets_full.is_dir()
        else None,
    )


def entry_exists(workspace_dir: Path, reference: str) -> bool:
    """Return whether a logical entry has any workspace files on disk.

    SQL-backed metadata is no longer consulted here — workspace
    presence is the on-disk signal.  Callers needing the SQL view
    should query :mod:`hivegent.db.documents` directly.
    """
    resolved = resolve_entry_paths(workspace_dir, reference)
    if (workspace_dir / resolved.description_path).exists():
        return True
    if resolved.original_path is not None:
        return True
    return resolved.assets_dir is not None

