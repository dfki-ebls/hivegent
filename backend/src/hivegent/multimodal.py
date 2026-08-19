"""Foundational multimodal-content primitives.

A dependency-free leaf so :mod:`hivegent.config` can type a setting against
:class:`BinaryContentMode` without importing the (now pool-coupled) converter
subsystem, which would close a config -> converters -> workers.pool -> config
import cycle.
"""

from enum import StrEnum

__all__ = ["BinaryContentMode"]


class BinaryContentMode(StrEnum):
    """How binary content reaches the chat model.

    The agent's binary reader can carry images, PDFs, and video.  This
    policy selects the representation:

    - :attr:`IMAGES` rasterises PDFs to one image per page, the only
      multimodal content type OpenAI-compatible vision servers (vLLM,
      SGLang, ...) accept — they reject the native ``file`` part outright.
    - :attr:`NATIVE` forwards PDF bytes with their ``application/pdf``
      media type, for providers with first-class document understanding
      (OpenAI, Anthropic) that ingest ``file`` parts directly.

    Images are always sent as images and time-based media (video,
    animations) is always sampled to frames either way, because no chat
    model ingests those containers natively.
    """

    IMAGES = "images"
    NATIVE = "native"
