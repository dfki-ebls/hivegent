"""Binary document reader for images, PDFs, and videos.

Surfaces visual content to multimodal models as tool output, so the
agent can inspect a chart, diagram, or page layout that the textual
conversion failed to capture.  Images pass through as images and
time-based media (video, animations) is always sampled to still frames,
because no chat model ingests those containers natively.  PDFs follow
:class:`BinaryContentMode`: rasterised to one image per page for vision
servers that only accept ``image_url`` parts (vLLM, SGLang, ...), or
forwarded as ``application/pdf`` for providers that ingest ``file``
parts directly (OpenAI, Anthropic).
"""

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, override

from pydantic import Field

from ..converters import vision_media_type
from ..converters.images import sanitize_image_bytes
from ..converters.pdf_raster import (
    DEFAULT_MAX_PAGES,
    extract_pdf_pages,
    render_pdf_pages,
)
from ..converters.video import (
    FRAME_MAX_DIMENSION,
    MAX_FRAMES,
    MediaSample,
    animation_frame_count,
    sample_animated_image,
    sample_video,
)
from ..multimodal import BinaryContentMode
from .base import (
    AsyncPathTool,
    BinaryAttachment,
    ToolOutput,
    ToolRetry,
    resolve_file_or_retry,
    sidecar_hint,
)

__all__ = [
    "BinaryReadResult",
    "ReadBinaryDocumentTool",
]

_MAX_BYTES = 20 * 1024 * 1024

BinaryFilePathArg = Annotated[
    str,
    Field(description="Full workspace path of the image, PDF, or video to read."),
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


@dataclass(slots=True, frozen=True)
class BinaryReadResult:
    """Summary of a binary document read."""

    file_path: str
    media_type: str
    size: int
    pages: tuple[int, ...] = ()
    frames: int = 0
    duration: float | None = None


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

    binary_content_mode: BinaryContentMode = BinaryContentMode.IMAGES
    """Whether PDFs are rasterised to images or forwarded as ``file`` parts."""

    max_bytes: int = _MAX_BYTES
    """Size cap for the source file read off disk (images, PDFs)."""

    max_pages: int = DEFAULT_MAX_PAGES
    """Upper bound of PDF pages rasterised into image attachments."""

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
        layout, photo, animation) can convey.  The visuals are attached
        inline with the tool return for the model to inspect.

        Supported types: PDF, PNG, JPEG, GIF, WebP, MP4, WebM, MOV, MKV.
        Videos and animated images are represented by a bounded set of
        still frames sampled evenly across their timeline, each labeled
        with its timestamp.  For PDFs, pass ``pages`` to select a subset
        (e.g. ``"3"``, ``"2-5"``, ``"1,3,5-7"``); omit it to read the
        whole document.  ``pages`` is rejected for non-PDF inputs.
        """
        sp, local, absolute = resolve_file_or_retry(self.resolved_paths, file_path)
        canonical = sp.prefixed(local)

        media_type = vision_media_type(local)
        if media_type is None:
            # Not every unsupported input is text: an Office document is neither
            # showable to a vision model nor readable by read_document, so point
            # at the entry's extracted text rather than bouncing the caller to a
            # tool that would refuse it too.
            raise ToolRetry(
                f"'{file_path}' is not a format a vision model can be shown — "
                f"use read_document if it is text.{sidecar_hint(canonical)}"
            )

        if pages is not None and media_type != "application/pdf":
            raise ToolRetry(f"pages= is only valid for PDF inputs, got {media_type}.")

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

        if media_type == "application/pdf":
            return await self._read_pdf(canonical, raw, pages)

        # Best-effort metadata strip; a quirky-but-storable image is returned
        # verbatim rather than raising, so a read never fails on sanitisation.
        raw = sanitize_image_bytes(raw, media_type)
        return ToolOutput(
            data=BinaryReadResult(
                file_path=canonical, media_type=media_type, size=len(raw)
            ),
            formatted=f"attached {canonical} ({media_type}, {len(raw)} bytes)",
            attachments=(
                BinaryAttachment(data=raw, media_type=media_type, identifier=canonical),
            ),
        )

    async def _read_pdf(
        self, canonical: str, raw: bytes, pages: str | None
    ) -> ToolOutput[BinaryReadResult]:
        """Surface a PDF as page images or a native ``file``, per the mode."""
        if self.binary_content_mode is BinaryContentMode.NATIVE:
            return await self._read_pdf_native(canonical, raw, pages)

        return await self._read_pdf_rendered(canonical, raw, pages)

    async def _read_pdf_rendered(
        self, canonical: str, raw: bytes, pages: str | None
    ) -> ToolOutput[BinaryReadResult]:
        """Rasterise the requested PDF pages into one image attachment each."""
        try:
            page_images, selected_pages = await render_pdf_pages(
                raw,
                pages,
                self.frame_max_dimension,
                self.max_pages,
            )
        except ValueError as exc:
            raise ToolRetry(f"PDF could not be read: {exc}") from exc

        attachments = tuple(
            BinaryAttachment(
                data=png, media_type="image/png", identifier=f"{canonical}#page={p}"
            )
            for p, png in zip(selected_pages, page_images, strict=True)
        )
        size = sum(len(a.data) for a in attachments)
        page_list = ",".join(str(p) for p in selected_pages)
        return ToolOutput(
            data=BinaryReadResult(
                file_path=canonical,
                media_type="application/pdf",
                size=size,
                pages=selected_pages,
            ),
            formatted=(
                f"rendered {len(attachments)} page(s) ({page_list}) from {canonical} "
                f"as images ({size} bytes)"
            ),
            attachments=attachments,
        )

    async def _read_pdf_native(
        self, canonical: str, raw: bytes, pages: str | None
    ) -> ToolOutput[BinaryReadResult]:
        """Forward a PDF (optionally a page subset) as a native ``file`` part."""
        selected_pages: tuple[int, ...] = ()
        if pages is not None:
            try:
                raw, selected_pages = await extract_pdf_pages(raw, pages)
            except ValueError as exc:
                raise ToolRetry(f"invalid pages: {exc}") from exc

        page_text = (
            f" pages {','.join(str(p) for p in selected_pages)}"
            if selected_pages
            else ""
        )
        return ToolOutput(
            data=BinaryReadResult(
                file_path=canonical,
                media_type="application/pdf",
                size=len(raw),
                pages=selected_pages,
            ),
            formatted=f"attached {canonical}{page_text} (application/pdf, {len(raw)} bytes)",
            attachments=(
                BinaryAttachment(
                    data=raw, media_type="application/pdf", identifier=canonical
                ),
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
