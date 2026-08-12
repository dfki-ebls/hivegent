"""Helpers for logical stem-based workspace entries."""

from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Self

from .converters import projects_verbatim
from .converters.base import DOCUMENT_EXTENSION, is_markdown_suffix

__all__ = [
    "ContentStat",
    "EntryPaths",
    "asset_ref_for",
    "assets_dir_for_stem",
    "description_path_for_stem",
    "entry_exists",
    "entry_owns",
    "find_original_for_stem",
    "is_assets_dir",
    "is_description_file",
    "is_ignorable_path",
    "is_inside_assets_dir",
    "is_projectable_original",
    "original_path_for_stem",
    "resolve_entry_paths",
    "stem_display_name",
    "stem_path_from_reference",
]


@dataclass(slots=True, frozen=True)
class ContentStat:
    """A markdown file's ``(mtime_ns, size)`` fingerprint for the reconcile fast-path.

    Lets the reconciler skip reading and hashing a description whose stat is
    unchanged since it was last indexed.  The content digest stays the
    authority: a stat mismatch only triggers a read + hash, and a re-embed
    happens solely when the digest itself differs, so a stat that lies in the
    "changed" direction (a ``touch``, checkout, or restore) costs one read, not
    a re-embed.
    """

    mtime_ns: int
    size: int

    @classmethod
    def from_path(cls, path: Path) -> Self | None:
        """Return the stat fingerprint of *path*, or ``None`` if it is unreadable."""
        try:
            st = path.stat()
        except OSError:
            return None
        return cls(mtime_ns=st.st_mtime_ns, size=st.st_size)


@dataclass(slots=True, frozen=True)
class EntryPaths:
    """Resolved paths for a logical stem entry."""

    stem_path: str
    description_path: str
    original_path: str | None
    assets_dir: str | None


def stem_path_from_reference(reference: str) -> str:
    """Return the logical stem path for a workspace-relative reference."""
    pure = PurePosixPath(reference)
    if pure.suffix:
        return str((pure.parent / pure.stem).as_posix())
    return str(pure.as_posix())


def is_assets_dir(name: str) -> bool:
    """Return whether a directory name is a child-assets directory."""
    return name.endswith(".assets")


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

    The scratch-versus-document policy seam: only markdown files map to
    logical document entries that the reconciler folds into SQL.  Every
    other on-disk file (originals, store-only assets, and any scratch output
    a future shell tool produces) is inert workspace content that is kept on
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
    >>> is_projectable_original("docs/diagram.svg")
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
    return path == stem_path or path == assets_dir or path.startswith(f"{assets_dir}/")


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


def find_original_for_stem(workspace_dir: Path, stem_path: str) -> str | None:
    """Return the workspace-relative original path for a logical stem.

    Takes a raw stem path, not a reference: a stem may itself contain dots
    (``a.tar`` from ``a.tar.gz``), so re-deriving it here via
    :func:`stem_path_from_reference` would strip part of the name and miss
    the entry's sibling files.
    """
    stem_pure = PurePosixPath(stem_path)
    parent_dir = workspace_dir / stem_pure.parent
    if not parent_dir.exists():
        return None

    candidates = sorted(
        candidate
        for candidate in parent_dir.iterdir()
        if candidate.is_file()
        and candidate.name != f"{stem_pure.name}{DOCUMENT_EXTENSION}"
        and candidate.stem == stem_pure.name
    )
    if not candidates:
        return None
    return str(candidates[0].relative_to(workspace_dir).as_posix())


def resolve_entry_paths(workspace_dir: Path, reference: str) -> EntryPaths:
    """Resolve logical entry paths from any workspace-relative reference."""
    stem_path = stem_path_from_reference(reference)
    assets_dir = assets_dir_for_stem(stem_path)
    assets_full = workspace_dir / assets_dir
    return EntryPaths(
        stem_path=stem_path,
        description_path=description_path_for_stem(stem_path),
        original_path=find_original_for_stem(workspace_dir, stem_path),
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


def stem_display_name(stem_path: str) -> str:
    """Return the user-facing basename for a logical stem."""
    return PurePosixPath(stem_path).name
