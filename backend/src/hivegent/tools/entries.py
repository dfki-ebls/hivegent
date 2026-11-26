"""Helpers for logical stem-based workspace entries."""

import os
from collections.abc import Iterable
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Literal, Self

from cbrkit.io import COMPANION_SUFFIXES, SUFFIXES, Companion, companion, subject
from exar.memory.mounts import Listdir, folds_case, list_names, respell

from ..shared.formats import DOCUMENT_EXTENSION, is_markdown_suffix
from ..shared.text import normalize_unicode
from ..shared.tree import tree

__all__ = [
    "CASE_BASE_KINDS",
    "ContentStat",
    "EntryPaths",
    "FileKind",
    "Listdir",
    "Part",
    "TreeStat",
    "asset_ref_for",
    "case_base_kinds",
    "entry_exists",
    "entry_owns",
    "find_original_for_stem",
    "folds_case",
    "is_assets_dir",
    "is_below",
    "is_case_base_file",
    "is_description_file",
    "is_ignorable_path",
    "is_inside_assets_dir",
    "is_reserved_path",
    "leads_with",
    "list_names",
    "path_key",
    "rebase",
    "repoint_asset_refs",
    "resolve_entry_paths",
    "resolve_tree_paths",
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

    @property
    def modified_at(self) -> datetime:
        """When the file's content last changed."""
        return datetime.fromtimestamp(self.mtime_ns / 1e9, UTC)

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


@dataclass(slots=True, frozen=True)
class TreeStat:
    """The relative paths and stat fingerprints of a directory and its contents."""

    parts: tuple[tuple[str, ContentStat], ...]

    @classmethod
    def from_path(cls, path: Path) -> Self | None:
        """Snapshot *path* without following symlinks, or return ``None`` if unreadable."""
        if not path.is_dir() or path.is_symlink():
            return None

        try:
            parts = (
                (str(part.relative_to(path)), ContentStat.of(part.lstat()))
                for part in tree(path)
            )

            return cls(tuple(sorted(parts)))
        except OSError:
            return None


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


def leads_with(root: Path, local: str, name: str | None) -> bool:
    """Whether *local* is or lies in the folder *name* directly below *root*.

    Compared the way the filesystem holding *root* compares names,
    so a change cannot reach the folder by another case.
    """
    top = PurePosixPath(local).parts[:1]

    if name is None or not top:
        return False

    folded = folds_case(root)

    return path_key(top[0], folded=folded) == path_key(name, folded=folded)


type FileKind = Literal["description", "original", "assets"] | Companion
"""What a workspace file is to its subject, see :class:`Part`.

A description is the markdown an entry is indexed by, an original the file it
was made from, and ``assets`` the payload folder extracted from it.  The
cbrkit companions (a sidecar, a cache, a lock) belong to the case base file or
folder they are named after the way an ``.assets`` payload belongs to its
entry: never indexed, never a case, hidden from listings, and moved and
deleted along with it.
"""

CASE_BASE_KINDS: frozenset[FileKind] = frozenset(COMPANION_SUFFIXES)
"""The kinds cbrkit keeps beside a case base, none of which hivegent writes."""

_KINDS: tuple[FileKind, ...] = (
    "description",
    "original",
    "assets",
    *COMPANION_SUFFIXES,
)
_ASSETS_SUFFIX = ".assets"


@dataclass(slots=True, frozen=True)
class Part:
    """One file of a subject, the single relation between a subject and its companions.

    A subject is a stem with the suffix of its original, and :attr:`path`
    derives every companion from it, while :meth:`of` is the inverse, telling
    which subject a path belongs to and as which kind.  A folder case base is
    a subject whose stem is the folder and whose suffix is empty.

    >>> [Part("data/cars", kind, ".csv").path for kind in ("description", "original", "assets")]
    ['data/cars.md', 'data/cars.csv', 'data/cars.assets']
    >>> Part.of(Part("data/cars", "sidecar", ".csv").path)
    Part(stem_path='data/cars', kind='sidecar', suffix='.csv')
    """

    stem_path: str
    kind: FileKind
    suffix: str = ""
    """The suffix of the subject's original, empty for one without."""

    @property
    def subject(self) -> str:
        """The path of the subject's original, the case base a cbrkit companion configures."""
        return f"{self.stem_path}{self.suffix}"

    @property
    def path(self) -> str:
        """The path of this part."""
        match self.kind:
            case "description":
                return f"{self.stem_path}{DOCUMENT_EXTENSION}"
            case "original":
                return self.subject
            case "assets":
                return f"{self.stem_path}{_ASSETS_SUFFIX}"
            case _:
                return companion(self.subject, self.kind).as_posix()

    @classmethod
    def of(cls, path: str) -> Self:
        """The part *path* is, judged by its name alone.

        >>> Part.of("data/cars.csv.cbrkit.json"), Part.of("a.md").kind, Part.of("data/.cbrkit.json").kind
        (Part(stem_path='data/cars', kind='sidecar', suffix='.csv'), 'description', 'original')
        """
        found = subject(path)

        if found is not None:
            kind, casebase = found
            original = cls.of(casebase.as_posix())

            return cls(original.stem_path, kind, original.suffix)

        stem = stem_path_from_reference(path)

        if is_description_file(path):
            return cls(stem, "description")

        if is_assets_dir(path):
            return cls(stem, "assets")

        return cls(stem, "original", PurePosixPath(path).suffix)


@dataclass(slots=True, frozen=True)
class EntryPaths:
    """A subject and the kinds of parts it has, every path derived by :class:`Part`.

    An entry always counts its description, which its row is keyed on, and
    a tree, a directory or a plain file, is a subject whose original is itself.
    """

    stem_path: str
    kinds: frozenset[FileKind] = frozenset({"description"})
    suffix: str = ""
    """The suffix of the original, empty for none."""

    @classmethod
    def entry(cls, stem_path: str, original: str | None = None) -> Self:
        """The entry at *stem_path* with its description and *original*, if any."""
        if original is None:
            return cls(stem_path)

        return cls(
            stem_path, frozenset({"description", "original"}), Part.of(original).suffix
        )

    @classmethod
    def tree(cls, path: str, kinds: Iterable[FileKind] = ()) -> Self:
        """The directory or plain file at *path* with the companions of *kinds*."""
        return cls(path, frozenset({"original", *kinds}))

    def path(self, kind: FileKind) -> str:
        """The path of the part of *kind*, whether it has one or not."""
        return Part(self.stem_path, kind, self.suffix).path

    def get(self, kind: FileKind) -> str | None:
        """The path of the part of *kind*, ``None`` when it has none."""
        return self.path(kind) if kind in self.kinds else None

    @property
    def files(self) -> tuple[str, ...]:
        """The paths of every part it has, the description first."""
        return tuple(self.path(kind) for kind in _KINDS if kind in self.kinds)

    @property
    def description_path(self) -> str:
        """The description an entry is indexed by."""
        return self.path("description")

    @property
    def original_path(self) -> str | None:
        """The original the entry was made from, if it has one."""
        return self.get("original")

    @property
    def assets_dir(self) -> str | None:
        """The payload folder extracted from the original, if it has one."""
        return self.get("assets")

    @property
    def source_path(self) -> str:
        """The original the entry was made from, else its description."""
        return self.original_path or self.description_path

    def at(self, stem_path: str) -> Self:
        """The subject relocated to *stem_path*, every part it has renamed along.

        >>> EntryPaths("a.tar", frozenset({"description", "original", "sidecar"}), ".gz").at("b/c").files
        ('b/c.md', 'b/c.gz', 'b/c.gz.cbrkit.json')
        """
        return replace(self, stem_path=stem_path)


def is_case_base_file(path: str) -> bool:
    """Whether the file at *path* may be a case base, cbrkit's ``is_casebase`` of a file told without a stat.

    >>> is_case_base_file("data/cars.CSV"), is_case_base_file(Part("data/cars", "sidecar", ".csv").path)
    (True, False)
    """
    return (
        Part.of(path).kind == "original"
        and PurePosixPath(path).suffix.lower() in SUFFIXES
    )


def case_base_kinds(workspace_dir: Path, subject: str) -> frozenset[FileKind]:
    """The cbrkit companions the case base at *subject* has on disk."""
    return frozenset(
        kind
        for kind in CASE_BASE_KINDS
        if (workspace_dir / Part(subject, kind).path).is_file()
    )


def resolve_tree_paths(workspace_dir: Path, path: str) -> EntryPaths:
    """The directory or file at *path* with the cbrkit companions it has on disk."""
    return EntryPaths.tree(path, case_base_kinds(workspace_dir, path))


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
    return PurePosixPath(name).suffix == _ASSETS_SUFFIX


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
    assets_dir = Part(stem_path, "assets").path

    return path in (stem_path, assets_dir) or is_below(path, assets_dir)


def asset_ref_for(assets_dir: str, relpath: str) -> str:
    """Return the in-markdown reference for an extracted asset.

    Assets are referenced relative to the description's sibling ``.assets``
    directory as ``<assets-dir-basename>/<relpath>``, the form that round-trips
    with the reference rewriting in
    :func:`workspace.prepare._relink_assets`.

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
    return markdown.replace(
        f"{Part(src_name, 'assets').path}/", f"{Part(dst_name, 'assets').path}/"
    )


def find_original_for_stem(
    workspace_dir: Path, stem_path: str, listdir: Listdir = list_names
) -> str | None:
    """Return the workspace-relative original path for a logical stem.

    Takes a raw stem path, not a reference: a stem may itself contain dots
    (``a.tar`` from ``a.tar.gz``), so re-deriving it here via
    :func:`stem_path_from_reference` would strip part of the name and miss
    the entry's sibling files.

    The name tests run before ``is_file``, which is the only predicate
    that costs a syscall: a plain prefix test rejects nearly every entry before
    the parse of its name, and the parse the handful sharing the stem, so the
    scan is one directory listing rather than one ``stat`` per file in it.  *listdir* lets a caller resolving many stems of one folder
    list it once.
    """
    stem_pure = PurePosixPath(stem_path)
    parent = stem_pure.parent
    candidates = sorted(
        name
        for name in listdir(workspace_dir / parent)
        if name.startswith(stem_pure.name)
        and Part.of(name)
        == Part(stem_pure.name, "original", PurePosixPath(name).suffix)
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
    original = find_original_for_stem(workspace_dir, stem_path, listdir)
    entry = EntryPaths.entry(stem_path, original)
    kinds = set(entry.kinds)

    if original is not None:
        kinds |= case_base_kinds(workspace_dir, original)

    if (workspace_dir / entry.path("assets")).is_dir():
        kinds.add("assets")

    return replace(entry, kinds=frozenset(kinds))


def entry_exists(workspace_dir: Path, reference: str) -> bool:
    """Return whether a logical entry has any workspace files on disk.

    SQL-backed metadata is no longer consulted here.  Workspace
    presence is the on-disk signal.  Callers needing the SQL view
    should query :mod:`hivegent.db.documents` directly.
    """
    resolved = resolve_entry_paths(workspace_dir, reference)
    if (workspace_dir / resolved.description_path).exists():
        return True
    if resolved.original_path is not None:
        return True
    return resolved.assets_dir is not None
