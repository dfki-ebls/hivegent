"""Binary document reader for images and PDFs.

Surfaces raw image and PDF bytes to vision-capable models as multimodal
tool output, so the agent can inspect a chart, diagram, or page layout
that the textual conversion failed to capture.
"""

import io
import re
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Annotated, override

from pydantic import Field

from ..converters.images import sanitize_image_bytes
from .base import (
    WORKSPACE_PATH_HINT,
    BinaryAttachment,
    SyncPathTool,
    ToolOutput,
    ToolRetry,
    resolve_accessible_file,
)

__all__ = [
    "BINARY_MEDIA_TYPES",
    "BinaryReadResult",
    "ReadBinaryDocumentTool",
    "binary_media_type",
]

_MAX_BYTES = 20 * 1024 * 1024

BINARY_MEDIA_TYPES: dict[str, str] = {
    ".pdf": "application/pdf",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".gif": "image/gif",
}
"""Extension → media type for inputs vision models reliably accept."""


def binary_media_type(file_path: str) -> str | None:
    """Return the media type for *file_path* if it is a supported binary."""
    return BINARY_MEDIA_TYPES.get(PurePosixPath(file_path).suffix.lower())


BinaryFilePathArg = Annotated[
    str,
    Field(description=f"Path of the image or PDF to read. {WORKSPACE_PATH_HINT}"),
]
PagesArg = Annotated[
    str | None,
    Field(
        description=(
            "PDF only: pages to extract, 1-indexed. Accepts a single page "
            "(`3`), a range (`2-5`), or a comma-separated list (`1,3,5-7`). "
            "Omit to send the whole document. Rejected for non-PDF inputs."
        ),
    ),
]

# Bounded digit count keeps a malicious `pages='9'*1e9` from forcing CPython
# to allocate a multi-megabyte int before the range check rejects it.
_PAGE_TOKEN_RE = re.compile(r"^(\d{1,9})(?:-(\d{1,9}))?$")


@dataclass(slots=True, frozen=True)
class BinaryReadResult:
    """Summary of a binary document read."""

    file_path: str
    media_type: str
    size: int
    pages: tuple[int, ...] = ()


def _parse_pages(spec: str, total: int) -> tuple[int, ...]:
    """Parse a page spec like ``1,3,5-7`` into a tuple of 1-based pages."""
    seen: dict[int, None] = {}
    for raw in spec.split(","):
        match = _PAGE_TOKEN_RE.match(raw.strip())
        if not match:
            raise ValueError(f"Invalid page token: {raw.strip()!r}")
        start = int(match.group(1))
        end = int(match.group(2)) if match.group(2) else start
        if start < 1 or end < start:
            raise ValueError(f"Invalid page range: {raw.strip()!r}")
        if end > total:
            raise ValueError(f"Page {end} exceeds document length {total}")
        for p in range(start, end + 1):
            seen.setdefault(p, None)
    return tuple(seen)


def _extract_pdf_pages(pdf_bytes: bytes, spec: str) -> tuple[bytes, tuple[int, ...]]:
    """Return a new PDF containing only the requested pages."""
    import pypdfium2 as pdfium

    try:
        with (
            pdfium.PdfDocument(pdf_bytes) as src,
            pdfium.PdfDocument.new() as dst,
        ):
            pages = _parse_pages(spec, len(src))
            dst.import_pages(src, pages=[p - 1 for p in pages])
            buf = io.BytesIO()
            dst.save(buf)
            return buf.getvalue(), pages
    except pdfium.PdfiumError as exc:
        raise ValueError(f"PDF could not be opened: {exc}") from exc


@dataclass(slots=True, frozen=True)
class ReadBinaryDocumentTool(SyncPathTool[BinaryReadResult]):
    """Read an image or PDF as binary content for vision-capable models."""

    @override
    def __call__(
        self,
        file_path: BinaryFilePathArg,
        pages: PagesArg = None,
    ) -> ToolOutput[BinaryReadResult]:
        """Read an image or PDF and attach it to the tool result.

        Use this when the textual conversion of a document is missing
        information that only the original visual (chart, diagram,
        layout, photo) can convey.  The bytes are attached as
        multimodal content sent inline with the tool return.

        Supported types: PDF, PNG, JPEG, GIF, WebP.  For PDFs, pass
        ``pages`` to extract a subset (e.g. ``"3"``, ``"2-5"``,
        ``"1,3,5-7"``); omit it to send the whole document.  ``pages``
        is rejected for non-PDF inputs.
        """
        resolved = resolve_accessible_file(self.resolved_paths, file_path)
        if resolved is None or not resolved[2].is_file():
            raise ToolRetry(f"'{file_path}' not found.")
        sp, local, absolute = resolved

        media_type = binary_media_type(local)
        if media_type is None:
            raise ToolRetry(
                f"'{file_path}' is not a supported binary type — "
                "use read_document for text files."
            )

        if pages is not None and media_type != "application/pdf":
            raise ToolRetry(f"pages= is only valid for PDF inputs, got {media_type}.")

        if absolute.stat().st_size > _MAX_BYTES:
            raise ToolRetry(
                f"file too large: exceeds {_MAX_BYTES} byte limit — "
                "narrow with pages= for PDFs."
            )

        raw = absolute.read_bytes()
        selected_pages: tuple[int, ...] = ()

        if media_type == "application/pdf" and pages is not None:
            try:
                raw, selected_pages = _extract_pdf_pages(raw, pages)
            except ValueError as exc:
                raise ToolRetry(f"invalid pages: {exc}") from exc
        elif media_type.startswith("image/"):
            try:
                raw = sanitize_image_bytes(raw, media_type)
            except ValueError as exc:
                raise ToolRetry(f"image rejected: {exc}") from exc

        canonical = sp.prefixed(local)
        page_text = (
            f" pages {','.join(str(p) for p in selected_pages)}"
            if selected_pages
            else ""
        )
        return ToolOutput(
            data=BinaryReadResult(
                file_path=canonical,
                media_type=media_type,
                size=len(raw),
                pages=selected_pages,
            ),
            formatted=(
                f"attached {canonical}{page_text} ({media_type}, {len(raw)} bytes)"
            ),
            attachments=(
                BinaryAttachment(data=raw, media_type=media_type, identifier=canonical),
            ),
        )
