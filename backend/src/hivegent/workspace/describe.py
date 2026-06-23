"""Vision description envelope for images and videos.

Each entry that carries a visual original gets a markdown projection from
the aux vision model, with a single shared fallback: resolve the aux config,
short-circuit to the file stem when no model is configured, and turn any
failure into the stem so the entry still gets a searchable projection.
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable, Sequence
from pathlib import Path, PurePosixPath

from ..converters.asset_processing import caption_frames, caption_image
from ..converters.video import (
    animation_frame_count,
    sample_animated_image,
    sample_video,
)
from ..types import LlmConfig, resolve_llm_config

__all__: list[str] = []

logger = logging.getLogger(__name__)


async def _describe_with_fallback(
    filepath: str,
    media_kind: str,
    llm: LlmConfig,
    describe: Callable[[LlmConfig], Awaitable[str]],
) -> str:
    """Run *describe* against the aux model, falling back to the file stem.

    Centralizes the description envelope shared by every vision entry:
    resolve the aux config, short-circuit to the stem when no model is
    configured, and turn any failure (or empty output) into the stem so
    the entry still gets a searchable projection.  *media_kind* only
    labels the warning log.
    """
    aux = resolve_llm_config(llm)
    fallback = PurePosixPath(filepath).stem
    if not aux.model:
        return f"{fallback}\n"
    try:
        description = await describe(aux)
    except Exception:
        logger.warning(
            "%s description generation failed for %s",
            media_kind,
            filepath,
            exc_info=True,
        )
        description = fallback
    return f"{description.strip() or fallback}\n"


async def _build_image_description(
    filepath: str,
    content: bytes,
    media_type: str,
    contexts: Sequence[str],
    llm: LlmConfig,
) -> str:
    """Generate markdown describing an image, grounded in *contexts*, with fallback.

    Animated images (multi-frame GIF/WebP) are captioned from frames
    sampled across their timeline rather than from the container bytes —
    vision models would otherwise see only the first frame, and a large
    animation would blow the provider's request size limit.
    """

    async def describe(aux: LlmConfig) -> str:
        if not media_type:
            return ""
        if await asyncio.to_thread(animation_frame_count, content, media_type) > 1:
            sample = await asyncio.to_thread(sample_animated_image, content)
            return await caption_frames(sample, contexts, aux)
        return await caption_image(content, media_type, contexts, aux)

    return await _describe_with_fallback(filepath, "Image", llm, describe)


async def _build_video_description(
    filepath: str,
    full_path: Path,
    contexts: Sequence[str],
    llm: LlmConfig,
) -> str:
    """Generate markdown describing a video from sampled frames, with fallback."""

    async def describe(aux: LlmConfig) -> str:
        sample = await sample_video(full_path)
        return await caption_frames(sample, contexts, aux)

    return await _describe_with_fallback(filepath, "Video", llm, describe)
