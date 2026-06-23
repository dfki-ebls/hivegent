"""Binary document reader for images, PDFs, and videos.

Surfaces raw image and PDF bytes to vision-capable models as multimodal
tool output, so the agent can inspect a chart, diagram, or page layout
that the textual conversion failed to capture.  Animated images and
videos are represented as a bounded set of frames sampled evenly across
their timeline, because vision chat models only accept still images.
"""

import asyncio
import io
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Annotated, override

from pydantic import Field

from ..converters.images import sanitize_image_bytes
from ..converters.video import (
    FRAME_MAX_DIMENSION,
    MAX_FRAMES,
    VIDEO_MEDIA_TYPES,
    MediaSample,
    animation_frame_count,
    sample_animated_image,
    sample_video,
)
from .base import (
    WORKSPACE_PATH_HINT,
    AsyncPathTool,
    BinaryAttachment,
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
    **VIDEO_MEDIA_TYPES,
}
"""Extension → media type for inputs this tool can surface to vision models."""


def binary_media_type(file_path: str) -> str | None:
    """Return the media type for *file_path* if it is a supported binary."""
    return BINARY_MEDIA_TYPES.get(PurePosixPath(file_path).suffix.lower())


BinaryFilePathArg = Annotated[
    str,
    Field(
        description=f"Path of the image, PDF, or video to read. {WORKSPACE_PATH_HINT}"
    ),
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
    frames: int = 0
    duration: float | None = None


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


def _frame_attachments(
    sample: MediaSample, canonical: str
) -> tuple[BinaryAttachment, ...]:
    """Build one PNG attachment per sampled frame, stamped with its timestamp."""
    return tuple(
        BinaryAttachment(
            data=frame.data,
            media_type="image/png",
            identifier=f"{canonical}#t={frame.timestamp:.1f}s",
        )
        for frame in sample.frames
    )


@dataclass(slots=True, frozen=True)
class ReadBinaryDocumentTool(AsyncPathTool[BinaryReadResult]):
    """Read an image, PDF, or video as binary content for vision models."""

    max_bytes: int = _MAX_BYTES
    """Size cap for media sent to the model verbatim (images, PDFs)."""

    max_frames: int = MAX_FRAMES
    """Upper bound of frames sampled from a video or animation."""

    frame_max_dimension: int = FRAME_MAX_DIMENSION
    """Maximum width/height of a sampled frame in pixels."""

    @override
    async def __call__(
        self,
        file_path: BinaryFilePathArg,
        pages: PagesArg = None,
    ) -> ToolOutput[BinaryReadResult]:
        """Read an image, PDF, or video and attach it to the tool result.

        Use this when the textual conversion of a document is missing
        information that only the original visual (chart, diagram,
        layout, photo, animation) can convey.  The bytes are attached
        as multimodal content sent inline with the tool return.

        Supported types: PDF, PNG, JPEG, GIF, WebP, MP4, WebM, MOV,
        MKV.  Videos and animated images are represented by a bounded
        set of still frames sampled evenly across their timeline, each
        labeled with its timestamp.  For PDFs, pass ``pages`` to
        extract a subset (e.g. ``"3"``, ``"2-5"``, ``"1,3,5-7"``);
        omit it to send the whole document.  ``pages`` is rejected for
        non-PDF inputs.
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

        canonical = sp.prefixed(local)

        if media_type.startswith("video/"):
            return await self._read_video(canonical, absolute, media_type)

        raw = absolute.read_bytes()

        if media_type.startswith("image/"):
            frame_count = await asyncio.to_thread(
                animation_frame_count, raw, media_type
            )
            if frame_count > 1:
                return await self._read_animation(canonical, raw, media_type)

        if len(raw) > self.max_bytes:
            raise ToolRetry(
                f"file too large: exceeds {self.max_bytes} byte limit — "
                "narrow with pages= for PDFs."
            )

        selected_pages: tuple[int, ...] = ()
        if media_type == "application/pdf" and pages is not None:
            try:
                raw, selected_pages = _extract_pdf_pages(raw, pages)
            except ValueError as exc:
                raise ToolRetry(f"invalid pages: {exc}") from exc
        elif media_type.startswith("image/"):
            # Best-effort metadata strip; a quirky-but-storable image is returned
            # verbatim rather than raising, so a read never fails on sanitisation.
            raw = sanitize_image_bytes(raw, media_type)

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

    async def _read_video(
        self, canonical: str, absolute: Path, media_type: str
    ) -> ToolOutput[BinaryReadResult]:
        """Sample a video file into timestamped frame attachments."""
        try:
            sample = await sample_video(
                absolute,
                max_frames=self.max_frames,
                max_dimension=self.frame_max_dimension,
            )
        except Exception as exc:
            raise ToolRetry(f"video could not be decoded: {exc}") from exc
        return self._sampled_output(canonical, media_type, sample)

    async def _read_animation(
        self, canonical: str, raw: bytes, media_type: str
    ) -> ToolOutput[BinaryReadResult]:
        """Sample an animated GIF/WebP into timestamped frame attachments."""
        try:
            sample = await asyncio.to_thread(
                sample_animated_image,
                raw,
                max_frames=self.max_frames,
                max_dimension=self.frame_max_dimension,
            )
        except ValueError as exc:
            raise ToolRetry(f"animation rejected: {exc}") from exc
        return self._sampled_output(canonical, media_type, sample)

    def _sampled_output(
        self, canonical: str, media_type: str, sample: MediaSample
    ) -> ToolOutput[BinaryReadResult]:
        """Build the tool output for frame-sampled media."""
        attachments = _frame_attachments(sample, canonical)
        size = sum(len(a.data) for a in attachments)
        return ToolOutput(
            data=BinaryReadResult(
                file_path=canonical,
                media_type=media_type,
                size=size,
                frames=len(attachments),
                duration=sample.duration,
            ),
            formatted=(
                f"attached {len(attachments)} frames sampled evenly from "
                f"{canonical} ({media_type}, duration {sample.duration:.1f}s); "
                "each frame is labeled with its timestamp"
            ),
            attachments=attachments,
        )
