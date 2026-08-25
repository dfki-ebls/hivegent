"""Pandoc-based document converter for miscellaneous document formats."""

import re
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from ..subprocesses import pandoc_convert
from .base import (
    ConversionResult,
    DocumentConverter,
    collect_dir_images,
    is_external_ref,
)
from .formats import PANDOC_FORMAT_OVERRIDES, PANDOC_SANDBOX_INCOMPATIBLE

__all__ = ["PandocConverter", "PandocConverterConfig"]

# Pandoc emits a bracketed attribute span (``[text]{attrs}``) for any linked
# image whose source it could not fetch. ``]{`` only follows a span, never an
# image-with-attributes (``![alt](src){attrs}``), so this never matches images.
_PLACEHOLDER_SPAN_RE = re.compile(r"(?<!!)\[([^\]]*)\]\{([^}]*)\}")


class PandocConverterConfig(BaseModel):
    """Configuration for the Pandoc conversion pipeline."""

    model_config = ConfigDict(extra="forbid")


def _normalize_media_refs(markdown: str, media_path: Path) -> str:
    """Strip pandoc's machine-local path leaks from converted markdown.

    Two distinct leaks are repaired:

    * ``--extract-media`` echoes its (absolute, temporary) extraction directory
      verbatim into every embedded-image link. Removing that prefix restores
      the ``media/...`` form whose keys match :func:`collect_dir_images`, so the
      downstream asset-rewrite step can localize the image.
    * A linked (non-embedded) image whose source pandoc cannot fetch becomes a
      placeholder span carrying ``original-image-src`` or the bare external
      path. Those files exist only on the author's machine, so the span is
      dropped.
    """
    for prefix in (f"{media_path.as_posix()}/", f"{media_path.as_uri()}/"):
        markdown = markdown.replace(prefix, "")

    def _drop_placeholder(match: re.Match[str]) -> str:
        text, attrs = match.group(1), match.group(2)
        if "original-image-src" in attrs or is_external_ref(text):
            return ""
        return match.group(0)

    return _PLACEHOLDER_SPAN_RE.sub(_drop_placeholder, markdown)


@dataclass(slots=True, frozen=True)
class PandocConverter(DocumentConverter):
    """Document converter using pandoc as an async subprocess.

    Supports a wide range of document formats including ODT, RST, RTF, EPUB,
    LaTeX, Org-mode, DocBook, Typst, wiki markups, bibliography formats, and
    more. Also handles DOCX, PPTX, and XLSX as an alternative to the
    specialized converters (Docling, Marker, MinerU).
    """

    name = "pandoc"
    config: PandocConverterConfig = field(default_factory=PandocConverterConfig)

    async def _convert(self, path: Path, /) -> ConversionResult:
        suffix = path.suffix.lower()
        use_sandbox = suffix not in PANDOC_SANDBOX_INCOMPATIBLE

        # Formats with embedded media benefit from --extract-media.
        if suffix in PANDOC_SANDBOX_INCOMPATIBLE:
            with tempfile.TemporaryDirectory() as media_dir:
                media_path = Path(media_dir)
                markdown = await pandoc_convert(
                    path,
                    from_format=PANDOC_FORMAT_OVERRIDES.get(suffix),
                    sandbox=use_sandbox,
                    extra_args=[f"--extract-media={media_path}"],
                )
                markdown = _normalize_media_refs(markdown, media_path)
                image_data = collect_dir_images(media_path, media_path)
                return ConversionResult(markdown=markdown, images=image_data)

        markdown = await pandoc_convert(
            path,
            from_format=PANDOC_FORMAT_OVERRIDES.get(suffix),
            sandbox=use_sandbox,
        )
        return ConversionResult(markdown=markdown)
