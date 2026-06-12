"""Asset processing pipeline: triage, identity, and context-aware captioning.

Three converter-agnostic optimizations decide whether and how to spend
vision-model time on an extracted image:

1. The asset's normalized :class:`~hivegent.converters.base.AssetRole`
   (populated by each converter's driver from its own native labels).
   :attr:`AssetRole.DECORATIVE` always store-onlies, :attr:`AssetRole.INFORMATIVE`
   always describes.
2. Size/shape heuristics on the raw bytes for assets the converter
   leaves at :attr:`AssetRole.UNKNOWN`.  Tiny images and extreme
   aspect ratios are almost always UI chrome.
3. Perceptual-hash identity (:func:`perceptual_key`) so the multiple
   occurrences of one image within a single conversion collapse to a
   single joint caption and a single stored entry.

Captioning itself is context-aware: :func:`caption_image` is given the
surrounding text of every occurrence and asked to *describe* illustrative
images but *transcribe* load-bearing figures (tables, diagrams, charts).
"""

import asyncio
import logging
import re
from collections.abc import Sequence
from enum import Enum
from io import BytesIO

import PIL.Image
from pydantic_ai import BinaryContent

from ..agents.app import base_agent
from ..llm import model_from_config, thinking_model_settings
from ..types import LlmConfig
from .base import AssetRole, ExtractedImage
from .images import sanitize_image_bytes
from .video import MediaSample

__all__ = [
    "MD_IMAGE_RE",
    "TriageDecision",
    "caption_frames",
    "caption_image",
    "image_context_windows",
    "perceptual_key",
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


# --- Perceptual-hash identity -------------------------------------------------


def perceptual_key(data: bytes, size: int = 8) -> int | None:
    """Compute a 64-bit dHash identifying *data* up to near-duplication.

    Used to unify an image's multiple occurrences within one conversion so
    they share a single joint caption and a single stored entry.  Returns
    ``None`` if the image doesn't decode or is uniform enough that every dHash
    bit collapses to the same value — solid-color icons, blank spacers, and
    fully transparent placeholders otherwise all hash to the same key and would
    be merged into one entry.  ``None`` callers treat each occurrence as its
    own singleton.
    """
    try:
        with PIL.Image.open(BytesIO(data)) as img:
            gray = img.convert("L").resize(
                (size + 1, size), PIL.Image.Resampling.BILINEAR
            )
    except _IMAGE_DECODE_ERRORS:
        logger.debug("Failed to compute perceptual key", exc_info=True)
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


# --- Occurrence context -------------------------------------------------------

# Characters of surrounding prose captured on each side of an image reference.
_CONTEXT_WINDOW = 400


def _strip_image_syntax(text: str) -> str:
    """Drop markdown image nodes and collapse whitespace in a context window."""
    return re.sub(r"\s+", " ", MD_IMAGE_RE.sub(" ", text)).strip()


def image_context_windows(markdown: str) -> dict[str, list[str]]:
    """Map each referenced image path to the context of its occurrences.

    For every ``![alt](path)`` node, captures the alt text plus a bounded
    window of the surrounding prose (with neighbouring image nodes stripped).
    An image referenced more than once yields one context entry per reference,
    so :func:`caption_image` can be given every place the image appears.

    >>> ctx = image_context_windows("Click ![the gear](ui.png) to open settings.")
    >>> ctx["ui.png"]
    ['Alt text: the gear\\nSurrounding text: Click to open settings.']
    """
    result: dict[str, list[str]] = {}
    for match in MD_IMAGE_RE.finditer(markdown):
        alt = match.group(1).strip()
        ref = match.group(2).strip()
        before = markdown[max(0, match.start() - _CONTEXT_WINDOW) : match.start()]
        after = markdown[match.end() : match.end() + _CONTEXT_WINDOW]
        window = _strip_image_syntax(f"{before} {after}")
        parts = []
        if alt:
            parts.append(f"Alt text: {alt}")
        if window:
            parts.append(f"Surrounding text: {window}")
        result.setdefault(ref, []).append("\n".join(parts))
    return result


# --- Context-aware captioning -------------------------------------------------

_VISION_TIMEOUT_S = 120.0

_CAPTION_INSTRUCTIONS = (
    "You are writing the canonical, reusable description of an image from "
    "technical documentation. It is stored once and used both as alt text and "
    "as a standalone search result, so it must stand on its own.\n\n"
    "Write a faithful description:\n"
    "- If the image is a screenshot, photo, or illustration, describe what it "
    "shows in one or two sentences, grounded in the context below (name the "
    "specific product, screen, workflow, or step it depicts).\n"
    "- If the image is a table, chart, diagram, schematic, or other data "
    "figure, transcribe the information it carries: the values, labels, axes, "
    "units, and structure, so nothing that lives only in the figure is lost.\n\n"
    "Be factual and specific. Do not invent details that are not visible. Do "
    "not start with 'This image shows' or similar."
)


_ANIMATION_CAPTION_INSTRUCTIONS = (
    "You are writing the canonical, reusable description of a video or "
    "animation from technical documentation, based on still frames sampled "
    "evenly across its timeline (each labeled with its timestamp). It is "
    "stored once and used both as alt text and as a standalone search "
    "result, so it must stand on its own.\n\n"
    "Describe what the video shows and how it progresses over time: the "
    "setting, the actions or steps performed, any on-screen text, and the "
    "outcome. If it demonstrates a workflow (e.g. a screen recording), "
    "transcribe the steps in order.\n\n"
    "Be factual and specific. Do not invent details that are not visible. "
    "Do not start with 'This video shows' or similar."
)


def _build_caption_prompt(instructions: str, contexts: Sequence[str]) -> str:
    """Assemble a caption prompt, appending de-duplicated occurrence contexts."""
    cleaned = [c.strip() for c in contexts if c.strip()]
    if not cleaned:
        return instructions
    joined = "\n\n---\n\n".join(dict.fromkeys(cleaned))
    return (
        f"{instructions}\n\n"
        f"Context where this content appears in the documentation:\n{joined}"
    )


async def caption_image(
    image_bytes: bytes,
    media_type: str,
    contexts: Sequence[str],
    llm_options: LlmConfig,
) -> str:
    """Caption a single image once, jointly grounded in all its *contexts*.

    The caller is responsible for image identity: every occurrence of the same
    image (see :func:`perceptual_key`) is collected and its surrounding text
    passed in *contexts*, so one image yields exactly one caption — the single
    source of truth shared by all its occurrences.

    Thinking is disabled for this call (``thinking=False``): pydantic_ai
    discards a ``<think>`` block and keeps only the ``TextPart``, so on a
    small-context aux model an unbounded reasoning trace would otherwise fill
    the window before any description is emitted (raising
    ``UnexpectedModelBehavior``).  ``llm_options.max_tokens`` further bounds
    the output.

    Args:
        image_bytes: The raw image bytes.
        media_type: The MIME type of the image.
        contexts: Surrounding-text snippets, one per occurrence (may be empty).
        llm_options: LLM configuration with a vision-capable model.

    Returns:
        A concise description, or a faithful transcription for data figures.

    Raises:
        ValueError: If the PNG payload is structurally invalid.
        asyncio.TimeoutError: If the vision model does not respond within
            :data:`_VISION_TIMEOUT_S` seconds.
    """
    sanitized = sanitize_image_bytes(image_bytes, media_type)
    content = BinaryContent(data=sanitized, media_type=media_type)
    result = await asyncio.wait_for(
        base_agent.run(
            [_build_caption_prompt(_CAPTION_INSTRUCTIONS, contexts), content],
            model=model_from_config(llm_options),
            model_settings=thinking_model_settings(False, llm_options),
        ),
        timeout=_VISION_TIMEOUT_S,
    )
    return str(result.output).strip()


async def caption_frames(
    sample: MediaSample,
    contexts: Sequence[str],
    llm_options: LlmConfig,
) -> str:
    """Caption a video or animation from its sampled frames.

    The counterpart of :func:`caption_image` for animated media: the
    frames in *sample* (see :func:`~hivegent.converters.video.sample_video`
    and :func:`~hivegent.converters.video.sample_animated_image`) are sent
    to the vision model interleaved with their timestamps, asking for a
    description of how the content progresses over time.

    Args:
        sample: Evenly sampled frames with the source duration.
        contexts: Surrounding-text snippets, one per occurrence (may be empty).
        llm_options: LLM configuration with a vision-capable model.

    Returns:
        A concise description of the video or animation.

    Raises:
        asyncio.TimeoutError: If the vision model does not respond within
            :data:`_VISION_TIMEOUT_S` seconds.
    """
    prompt = _build_caption_prompt(_ANIMATION_CAPTION_INSTRUCTIONS, contexts)
    parts: list[str | BinaryContent] = [
        f"{prompt}\n\nTotal duration: {sample.duration:.1f}s."
    ]
    for frame in sample.frames:
        parts.append(f"Frame at {frame.timestamp:.1f}s:")
        parts.append(BinaryContent(data=frame.data, media_type="image/png"))
    result = await asyncio.wait_for(
        base_agent.run(
            parts,
            model=model_from_config(llm_options),
            model_settings=thinking_model_settings(False, llm_options),
        ),
        timeout=_VISION_TIMEOUT_S,
    )
    return str(result.output).strip()
