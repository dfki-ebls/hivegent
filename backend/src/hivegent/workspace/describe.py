"""Vision description envelope for images and videos.

Each entry that carries a visual original gets a markdown projection from
the aux vision model (`exar.ingest.caption`).
Without a model, or when it fails or answers nothing, there is no description,
so the caller writes no sidecar rather than one that only repeats the file name.
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable, Sequence
from functools import partial

from exar.ingest import (
    ANIMATED_MEDIA_TYPES,
    MAX_DIMENSION,
    MAX_FRAMES,
    Media,
    Sample,
    caption,
    sample_animation,
    sample_video,
)

from ..llm_config import aux_llm
from ..media import ISOLATED, source_on_disk
from ..pipelines.vision import Vision

__all__: list[str] = []

logger = logging.getLogger(__name__)


def _frame_budget(vision: Vision) -> int:
    """Frames one caption request of *vision* carries, within its model's image cap."""
    cap = vision.config.max_images

    return MAX_FRAMES if cap is None else min(MAX_FRAMES, cap)


async def _isolated[R](call: Callable[[], R]) -> R:
    """Run the decoder *call* in an isolated worker, whose failure leaves the media undescribed."""
    return await asyncio.get_running_loop().run_in_executor(ISOLATED, call)


async def _describe(
    filepath: str,
    media_kind: str,
    describe: Callable[[Vision], Awaitable[str]],
) -> str | None:
    """Run *describe* with the aux model, ``None`` without a model, on failure, or on empty output.

    *media_kind* only labels the warning log.
    """
    aux = aux_llm()

    if aux is None:
        return None

    try:
        description = (await describe(Vision(config=aux))).strip()
    except Exception:
        logger.warning(
            "%s description generation failed for %s",
            media_kind,
            filepath,
            exc_info=True,
        )

        return None

    return f"{description}\n" if description else None


async def _build_image_description(
    filepath: str,
    content: bytes,
    media_type: str,
    contexts: Sequence[str],
) -> str | None:
    """Generate markdown describing an image, grounded in *contexts*, ``None`` without one.

    Animated images (multi-frame GIF/WebP) are captioned from frames
    sampled across their timeline rather than from the container bytes,
    since vision models would otherwise see only the first frame, and a large
    animation would blow the provider's request size limit.
    """

    async def describe(vision: Vision) -> str:
        if not media_type:
            return ""

        sample: Sample | None = None

        # Only an animatable format is worth a worker process.
        if media_type in ANIMATED_MEDIA_TYPES:
            sample = await _isolated(
                partial(
                    sample_animation,
                    content,
                    max_frames=_frame_budget(vision),
                    max_dimension=MAX_DIMENSION,
                )
            )

        return await caption(
            vision,
            Media(data=content, media_type=media_type) if sample is None else sample,
            contexts,
        )

    return await _describe(filepath, "Image", describe)


async def _build_video_description(
    filepath: str,
    content: bytes,
    contexts: Sequence[str],
) -> str | None:
    """Generate markdown describing a video from sampled frames, ``None`` without one.

    The decoder reads a temp copy, written only once a model is there to see its frames.
    """

    async def describe(vision: Vision) -> str:
        with source_on_disk(filepath, content) as full_path:
            sample = await _isolated(
                partial(
                    sample_video,
                    full_path,
                    max_frames=_frame_budget(vision),
                    max_dimension=MAX_DIMENSION,
                )
            )

        return await caption(vision, sample, contexts)

    return await _describe(filepath, "Video", describe)
