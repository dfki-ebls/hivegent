"""Pure-Pillow image helpers safe to import from anywhere.

Kept dependency-light on purpose: only Pillow, no ``hivegent.converters``
imports.  A spawned isolation worker (see :mod:`hivegent.workers`) can pull
in just this module instead of the whole converter registry, which keeps
worker startup fast.
"""

from io import BytesIO

import PIL.Image

__all__ = ["pil_to_still_png"]


def pil_to_still_png(image: PIL.Image.Image, max_dimension: int) -> bytes:
    """Flatten, downscale, and PNG-encode a PIL image as a still.

    Bounds the longer side to *max_dimension*. Transparent pixels are
    composited onto white — vision models receive no alpha channel
    semantics, and black-flattened transparency makes light-on-transparent
    content unreadable. Shared by the video/animation frame samplers and
    the PDF page renderer so every still sent to a vision model is encoded
    identically.
    """
    rgba = image.convert("RGBA")
    background = PIL.Image.new("RGBA", rgba.size, (255, 255, 255, 255))
    still = PIL.Image.alpha_composite(background, rgba).convert("RGB")
    still.thumbnail((max_dimension, max_dimension))
    buf = BytesIO()
    still.save(buf, format="PNG")
    return buf.getvalue()
