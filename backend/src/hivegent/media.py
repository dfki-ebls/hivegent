"""The isolation of the decoders of the images, PDFs, and videos a vision model is shown, those of `exar.ingest`.

PDFs, animated images, and container video are decoded in a throwaway worker process (`ISOLATED`),
since pdfium, FFmpeg, and Pillow crash or hang on some malformed input and the input here is untrusted.
The binary reader refuses a crash or a timeout like unreadable input (`exar.tools.media.View.executor`),
instead of taking the server down with it.
"""

from exar.ingest import ProcessPool

from .config import settings
from .shared.formats import media_type

__all__ = ["ISOLATED", "guess_image_media_type"]

ISOLATED = ProcessPool(
    settings.isolation.max_workers,
    timeout=settings.isolation.timeout_seconds,
    max_tasks=1,
)
"""Runs every native decoder call of untrusted input in a fresh process."""


def guess_image_media_type(path: str) -> str | None:
    """Return the media type for an image path, or `None` when it is not one."""
    resolved = media_type(path)

    return resolved if resolved and resolved.startswith("image/") else None
