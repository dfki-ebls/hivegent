"""Asset processing pipeline: triage and deduped vision-model description.

Three converter-agnostic optimizations decide whether and how to spend
vision-model time on an extracted image:

1. The asset's normalized :class:`~hivegent.converters.base.AssetRole`
   (populated by each converter's driver from its own native labels).
   :attr:`AssetRole.DECORATIVE` always store-onlies, :attr:`AssetRole.INFORMATIVE`
   always describes.
2. Size/shape heuristics on the raw bytes for assets the converter
   leaves at :attr:`AssetRole.UNKNOWN`.  Tiny images and extreme
   aspect ratios are almost always UI chrome.
3. Perceptual-hash deduplication so a repeated logo, header, or
   footer is described at most once per process.
"""

import asyncio
import logging
import re
from collections import OrderedDict
from enum import Enum
from io import BytesIO

import PIL.Image
from pydantic_ai import BinaryContent

from ..agents.app import base_agent
from ..llm import model_from_config
from ..types import LlmConfig
from .base import AssetRole, ExtractedImage
from .images import sanitize_image_bytes

__all__ = [
    "MD_IMAGE_RE",
    "TriageDecision",
    "describe_image",
    "triage_image",
]

logger = logging.getLogger(__name__)

MD_IMAGE_RE = re.compile(r"!\[([^\]]*)\]\(([^)]+)\)")

# DecompressionBombError is a subclass of Exception but not of OSError or
# ValueError, so it must be listed explicitly to fall through to the heuristic
# path rather than crash the caller.
_IMAGE_DECODE_ERRORS = (
    PIL.UnidentifiedImageError,
    PIL.Image.DecompressionBombError,
    OSError,
    ValueError,
)


# --- Triage by role and shape -------------------------------------------------


class TriageDecision(str, Enum):
    """Whether to send an extracted asset to the vision model."""

    DESCRIBE = "describe"
    STORE_ONLY = "store_only"


_MIN_DIMENSION = 48
_MIN_PIXELS = 4096
_MAX_ASPECT_RATIO = 15.0
_MIN_FILE_SIZE = 2048


def _probe(data: bytes) -> tuple[int, int] | None:
    """Return ``(width, height)`` from a PIL header parse; ``None`` on failure."""
    try:
        with PIL.Image.open(BytesIO(data)) as img:
            return img.size
    except _IMAGE_DECODE_ERRORS:
        logger.debug("Failed to probe image dimensions", exc_info=True)
        return None


def _is_decorative_by_shape(data: bytes) -> bool:
    """Return ``True`` for tiny or extremely thin images."""
    if len(data) < _MIN_FILE_SIZE:
        return True
    probe = _probe(data)
    if probe is None:
        return False
    w, h = probe
    if min(w, h) < _MIN_DIMENSION:
        return True
    if w * h < _MIN_PIXELS:
        return True
    if max(w, h) / max(min(w, h), 1) > _MAX_ASPECT_RATIO:
        return True
    return False


def triage_image(image: ExtractedImage) -> TriageDecision:
    """Return whether *image* should be described or merely stored.

    Decision order:

    1. :attr:`AssetRole.INFORMATIVE` → describe.
    2. :attr:`AssetRole.DECORATIVE` → store only.
    3. :attr:`AssetRole.UNKNOWN` and bytes look decorative by size/shape →
       store only.
    4. Otherwise → describe.
    """
    if image.role is AssetRole.INFORMATIVE:
        return TriageDecision.DESCRIBE
    if image.role is AssetRole.DECORATIVE:
        return TriageDecision.STORE_ONLY
    if _is_decorative_by_shape(image.data):
        return TriageDecision.STORE_ONLY
    return TriageDecision.DESCRIBE


# --- Perceptual-hash deduplication --------------------------------------------

# Process-wide LRU dedup of in-flight and completed descriptions, keyed by
# ``(perceptual_hash, model, base_url, api_key, media_type)`` so results never
# leak across tenants, providers, or formats.  Holding the Future strong-refs
# the underlying Task; asyncio's single-threaded scheduler makes get/set/move
# atomic so no extra lock is required.  Pending entries are kept past the cap
# until they complete — evicting an in-flight future would defeat dedup.
type _CacheKey = tuple[int, str, str | None, str, str]
_DESCRIPTION_CACHE_MAX = 1024
_VISION_TIMEOUT_S = 120.0
_description_cache: OrderedDict[_CacheKey, asyncio.Future[str]] = OrderedDict()


def _image_hash(data: bytes, size: int = 8) -> int | None:
    """Compute a 64-bit dHash for *data*.

    Returns ``None`` if the image doesn't decode or is uniform enough that
    every dHash bit collapses to the same value — solid-color icons, blank
    spacers, and fully transparent placeholders otherwise all hash to the
    same key and would share a single description.
    """
    try:
        with PIL.Image.open(BytesIO(data)) as img:
            gray = img.convert("L").resize(
                (size + 1, size), PIL.Image.Resampling.BILINEAR
            )
    except _IMAGE_DECODE_ERRORS:
        logger.debug("Failed to compute image hash", exc_info=True)
        return None
    pixels = gray.tobytes()
    bits = 0
    for row in range(size):
        for col in range(size):
            left = pixels[row * (size + 1) + col]
            right = pixels[row * (size + 1) + col + 1]
            bits = (bits << 1) | (1 if left > right else 0)
    if bits == 0 or bits == (1 << (size * size)) - 1:
        return None
    return bits


def _evict_old_completed() -> None:
    """Drop the oldest *completed* entry when the cache exceeds its cap.

    Each insert grows the cache by at most one, so a single pass suffices.
    Pending entries are skipped so an in-flight description never loses its
    cache slot; the cache can temporarily exceed the cap until those futures
    resolve.
    """
    if len(_description_cache) <= _DESCRIPTION_CACHE_MAX:
        return
    for k, f in _description_cache.items():
        if f.done():
            del _description_cache[k]
            return


# --- Vision-model description -------------------------------------------------

_DESCRIBE_PROMPT = (
    "Describe this image in one concise sentence for use as alt text. "
    "Be factual and specific. Do not start with 'This image shows' or similar."
)


async def _invoke_vision_model(
    sanitized_bytes: bytes,
    media_type: str,
    llm_options: LlmConfig,
) -> str:
    """Run the vision model on a single already-sanitized image.

    Reasoning-capable models are expected to emit the final description
    after any ``<think>`` block — pydantic_ai aggregates the ``TextPart``
    content and discards thinking.  Disable thinking at the server (e.g.
    vLLM ``chat_template_kwargs``) if the chosen model otherwise produces
    thinking-only responses.
    """
    content = BinaryContent(data=sanitized_bytes, media_type=media_type)
    result = await base_agent.run(
        [_DESCRIBE_PROMPT, content],
        model=model_from_config(llm_options),
    )
    return str(result.output).strip()


def _is_poisoned(fut: asyncio.Future[str]) -> bool:
    """Return ``True`` if *fut* is done with a failure or cancellation."""
    return fut.done() and (fut.cancelled() or fut.exception() is not None)


async def describe_image(
    image_bytes: bytes,
    media_type: str,
    llm_options: LlmConfig,
) -> str:
    """Describe a single image, deduplicated by perceptual hash.

    Repeated logos, headers, or footers within a process lifetime share a
    single vision-model call.  ``asyncio.shield`` keeps awaiter cancellation
    from poisoning the cached future; an :data:`_VISION_TIMEOUT_S`-second
    wall-clock cap prevents a hung backend from wedging every later caller
    of the same dHash.  Failed, cancelled, or timed-out entries are evicted
    so a later request can retry.

    Args:
        image_bytes: The raw image bytes.
        media_type: The MIME type of the image.
        llm_options: LLM configuration with a vision model; the cache is
            scoped by ``model``, ``base_url``, ``api_key``, and
            ``media_type`` so results never leak across tenants, providers,
            or formats.

    Returns:
        A concise description string.

    Raises:
        ValueError: If the PNG payload is structurally invalid.
        asyncio.TimeoutError: If the vision model does not respond within
            :data:`_VISION_TIMEOUT_S` seconds.
    """
    sanitized = sanitize_image_bytes(image_bytes, media_type)
    image_hash = _image_hash(sanitized)
    if image_hash is None:
        return await asyncio.wait_for(
            _invoke_vision_model(sanitized, media_type, llm_options),
            timeout=_VISION_TIMEOUT_S,
        )
    key: _CacheKey = (
        image_hash,
        llm_options.model,
        llm_options.base_url,
        llm_options.api_key,
        media_type,
    )
    fut = _description_cache.get(key)
    if fut is not None and not _is_poisoned(fut):
        _description_cache.move_to_end(key)
    else:
        if fut is not None:
            _description_cache.pop(key, None)
        fut = asyncio.ensure_future(
            asyncio.wait_for(
                _invoke_vision_model(sanitized, media_type, llm_options),
                timeout=_VISION_TIMEOUT_S,
            )
        )
        _description_cache[key] = fut
        _evict_old_completed()

        def _evict_on_failure(f: asyncio.Future[str], k: _CacheKey = key) -> None:
            if _is_poisoned(f):
                _description_cache.pop(k, None)

        fut.add_done_callback(_evict_on_failure)
    return await asyncio.shield(fut)
