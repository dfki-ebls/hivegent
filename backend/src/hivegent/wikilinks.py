"""Wikilink normalization and attachment detection for markdown collections.

Handles Obsidian-style ``[[wikilinks]]`` and ``![[embeds]]``, converting
them to standard markdown links while detecting binary attachments that
need conversion.
"""

import re
from collections.abc import Callable, Set
from dataclasses import dataclass, field
from pathlib import PurePosixPath

from .config import DOCUMENT_EXTENSION
from .converters.alt_text import MD_IMAGE_RE

__all__ = [
    "IMAGE_EXTENSIONS",
    "PreprocessedMarkdown",
    "preprocess_markdown",
]

_EMBED_RE = re.compile(r"!\[\[(.+?)\]\]")
_WIKILINK_RE = re.compile(r"(?<!!)\[\[(.+?)\]\]")
IMAGE_EXTENSIONS = frozenset(
    {
        ".png",
        ".jpg",
        ".jpeg",
        ".gif",
        ".webp",
        ".svg",
        ".bmp",
        ".tiff",
        ".tif",
        ".ico",
    }
)


@dataclass(slots=True, frozen=True)
class PreprocessedMarkdown:
    """Result of preprocessing a markdown file.

    Attributes:
        content: The rewritten markdown with normalized links.
        binary_attachments: Set of relative paths (from collection root) to
            binary files that need conversion.
        image_attachments: Set of relative paths to image files that should
            be stored as-is in the workspace.
    """

    content: str
    binary_attachments: frozenset[str] = field(default_factory=frozenset)
    image_attachments: frozenset[str] = field(default_factory=frozenset)


def _resolve_target(
    target: str,
    source_dir: PurePosixPath,
    collection_files: Set[str],
) -> str | None:
    """Resolve a wikilink target to a file path in the collection.

    Tries exact match, relative to source dir, with extensions appended,
    and finally by filename (Obsidian-style shortest-path resolution).
    """
    target = target.replace("\\", "/")

    for candidate in (target, str(source_dir / target)):
        if candidate in collection_files:
            return candidate

    # Try appending .md — the most common implicit extension in wikilinks
    for base in (target, str(source_dir / target)):
        if base + ".md" in collection_files:
            return base + ".md"

    target_name = PurePosixPath(target).name
    for f in collection_files:
        p = PurePosixPath(f)
        if p.name == target_name or p.stem == target_name:
            return f

    return None


def _relative_link(target: str, source_dir: PurePosixPath) -> str:
    """Create a relative link from source_dir to target."""
    try:
        return str(PurePosixPath(target).relative_to(source_dir, walk_up=True))
    except ValueError:
        return target


def _rewrite_link(
    target: str,
    alias: str,
    source_dir: PurePosixPath,
    collection_files: Set[str],
    binary_attachments: set[str],
    image_attachments: set[str],
) -> str:
    """Resolve a link target and return a standard markdown link.

    Binary targets are recorded in *binary_attachments* and the link is
    rewritten to point to the future ``.md`` conversion output.  Image
    files are recorded in *image_attachments* instead and keep
    ``![]()`` syntax.
    """
    resolved = _resolve_target(target, source_dir, collection_files)
    if resolved is None:
        return f"[{alias}]"

    suffix = PurePosixPath(resolved).suffix.lower()
    if suffix != DOCUMENT_EXTENSION:
        if suffix in IMAGE_EXTENSIONS:
            image_attachments.add(resolved)
            rel = _relative_link(resolved, source_dir)
            return f"![{alias}]({rel})"
        binary_attachments.add(resolved)
        resolved = str(PurePosixPath(resolved).with_suffix(".md"))

    return f"[{alias}]({_relative_link(resolved, source_dir)})"


def _parse_pipe(
    raw: str, default_alias: Callable[[str], str]
) -> tuple[str, str] | None:
    """Parse ``target|alias`` syntax, returning ``None`` if target is empty."""
    if "|" in raw:
        target, alias = raw.split("|", 1)
        target, alias = target.strip(), alias.strip()
        return (target, alias or default_alias(target)) if target else None
    target = raw.strip()
    return (target, default_alias(target)) if target else None


def preprocess_markdown(
    content: str,
    source_path: str,
    collection_files: Set[str],
) -> PreprocessedMarkdown:
    """Preprocess markdown by normalizing wikilinks and detecting attachments.

    Converts Obsidian wikilinks and embeds to standard markdown links,
    detects binary references, and rewrites them to point to the future
    ``.md`` conversion output.

    Args:
        content: The raw markdown content.
        source_path: Relative path of this file within the collection.
        collection_files: Set of all file paths in the collection.

    Returns:
        A :class:`PreprocessedMarkdown` with rewritten content and binary
        attachment paths.
    """
    source_dir = PurePosixPath(source_path).parent
    binaries: set[str] = set()
    images: set[str] = set()

    def _on_embed(m: re.Match[str]) -> str:
        parsed = _parse_pipe(m.group(1), lambda t: PurePosixPath(t).stem)
        if not parsed:
            return m.group(0)
        return _rewrite_link(
            *parsed,
            source_dir,
            collection_files,
            binaries,
            images,
        )

    def _on_wikilink(m: re.Match[str]) -> str:
        parsed = _parse_pipe(m.group(1), lambda t: t)
        if not parsed:
            return m.group(0)
        return _rewrite_link(
            *parsed,
            source_dir,
            collection_files,
            binaries,
            images,
        )

    def _on_md_image(m: re.Match[str]) -> str:
        alt, path = m.group(1), m.group(2)
        if path.startswith(("http://", "https://", "data:")):
            return m.group(0)
        resolved = _resolve_target(path, source_dir, collection_files)
        if resolved and PurePosixPath(resolved).suffix.lower() != DOCUMENT_EXTENSION:
            suffix = PurePosixPath(resolved).suffix.lower()
            if suffix in IMAGE_EXTENSIONS:
                images.add(resolved)
                return f"![{alt}]({_relative_link(resolved, source_dir)})"
            binaries.add(resolved)
            md_path = str(PurePosixPath(resolved).with_suffix(".md"))
            return f"[{alt or PurePosixPath(resolved).stem}]({_relative_link(md_path, source_dir)})"
        return m.group(0)

    result = _EMBED_RE.sub(_on_embed, content)
    result = _WIKILINK_RE.sub(_on_wikilink, result)
    result = MD_IMAGE_RE.sub(_on_md_image, result)

    return PreprocessedMarkdown(
        content=result,
        binary_attachments=frozenset(binaries),
        image_attachments=frozenset(images),
    )
