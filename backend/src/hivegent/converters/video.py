"""Frame sampling for videos and animated images.

Vision chat models accept only still images, so animated media is
represented as a bounded set of frames sampled evenly across the
timeline, each downscaled to keep the request payload small.  Animated
GIF/WebP images are decoded in-process with Pillow; container video
formats go through ffmpeg (see :mod:`hivegent.subprocesses.ffmpeg`).
"""

from dataclasses import dataclass
from io import BytesIO
from pathlib import Path

import PIL.Image
from PIL import ImageSequence

from ..imaging import pil_to_still_png
from ..subprocesses.ffmpeg import extract_frame, probe_duration

__all__ = [
    "FRAME_MAX_DIMENSION",
    "MAX_FRAMES",
    "VIDEO_MEDIA_TYPES",
    "MediaSample",
    "SampledFrame",
    "animation_frame_count",
    "is_video_suffix",
    "sample_animated_image",
    "sample_video",
]

VIDEO_MEDIA_TYPES: dict[str, str] = {
    ".mp4": "video/mp4",
    ".webm": "video/webm",
    ".mov": "video/quicktime",
    ".mkv": "video/x-matroska",
}
"""Extension → media type for the supported video containers."""

# Image formats whose containers support multi-frame animation.
_ANIMATED_IMAGE_MEDIA_TYPES = frozenset({"image/gif", "image/webp"})

MAX_FRAMES = 8
"""Default upper bound of frames sampled from one media file."""

FRAME_MAX_DIMENSION = 1024
"""Default maximum width/height of a sampled frame in pixels."""

# Fallback per-frame duration for animations that omit timing metadata.
_DEFAULT_FRAME_MS = 100.0


def is_video_suffix(suffix: str) -> bool:
    """Return whether *suffix* matches a supported video extension."""
    return suffix.lower() in VIDEO_MEDIA_TYPES


@dataclass(slots=True, frozen=True)
class SampledFrame:
    """A single frame sampled from an animation or video."""

    timestamp: float
    """Position of the frame on the source timeline, in seconds."""

    data: bytes
    """PNG-encoded frame bytes, downscaled to the sampling dimension."""


@dataclass(slots=True, frozen=True)
class MediaSample:
    """Evenly sampled frames representing an animation or video."""

    frames: tuple[SampledFrame, ...]
    duration: float
    """Total duration of the source media, in seconds."""


def _sample_indices(count: int, max_frames: int) -> frozenset[int]:
    """Pick up to *max_frames* indices evenly spread over ``range(count)``.

    Uses segment midpoints so the selection covers the whole timeline
    without clustering at either end.

    >>> sorted(_sample_indices(4, 8))
    [0, 1, 2, 3]
    >>> sorted(_sample_indices(100, 4))
    [12, 37, 62, 87]
    """
    if count <= max_frames:
        return frozenset(range(count))
    return frozenset(int((i + 0.5) * count / max_frames) for i in range(max_frames))


def animation_frame_count(data: bytes, media_type: str) -> int:
    """Return the frame count if *data* is an animated image, else ``1``.

    Only GIF and WebP containers are probed; anything that fails to
    decode counts as a single frame so callers fall back to the static
    image path (where decoding errors surface with proper context).
    """
    if media_type not in _ANIMATED_IMAGE_MEDIA_TYPES:
        return 1
    try:
        with PIL.Image.open(BytesIO(data)) as img:
            return getattr(img, "n_frames", 1)
    except Exception:
        return 1


def sample_animated_image(
    data: bytes,
    *,
    max_frames: int = MAX_FRAMES,
    max_dimension: int = FRAME_MAX_DIMENSION,
) -> MediaSample:
    """Sample frames evenly across an animated GIF/WebP.

    Frames are decoded sequentially (required for correct frame
    compositing), but only the selected ones are flattened, downscaled,
    and encoded.

    Args:
        data: The raw animated image bytes.
        max_frames: Upper bound of frames to sample.
        max_dimension: Maximum width/height of each sampled frame.

    Returns:
        The sampled frames with their timeline positions.

    Raises:
        ValueError: If the image cannot be decoded.
    """
    frames: list[SampledFrame] = []
    elapsed_ms = 0.0
    try:
        with PIL.Image.open(BytesIO(data)) as img:
            wanted = _sample_indices(getattr(img, "n_frames", 1), max_frames)
            for index, frame in enumerate(ImageSequence.Iterator(img)):
                if index in wanted:
                    frames.append(
                        SampledFrame(
                            timestamp=elapsed_ms / 1000,
                            data=pil_to_still_png(frame, max_dimension),
                        )
                    )
                elapsed_ms += frame.info.get("duration") or _DEFAULT_FRAME_MS
    except PIL.UnidentifiedImageError as exc:
        raise ValueError(f"Animated image could not be decoded: {exc}") from exc
    return MediaSample(frames=tuple(frames), duration=elapsed_ms / 1000)


async def sample_video(
    path: Path,
    *,
    max_frames: int = MAX_FRAMES,
    max_dimension: int = FRAME_MAX_DIMENSION,
) -> MediaSample:
    """Sample frames evenly across a video file via ffmpeg.

    Samples roughly one frame per second for short clips, capped at
    *max_frames* for longer material; timestamps sit at segment
    midpoints so the selection spans the whole timeline.

    Args:
        path: The video file to sample.
        max_frames: Upper bound of frames to sample.
        max_dimension: Maximum width/height of each sampled frame.

    Returns:
        The sampled frames with their timeline positions.

    Raises:
        SubprocessError: If ffprobe/ffmpeg cannot read the file.
        ValueError: If the container reports no usable duration.
    """
    duration = await probe_duration(path)
    count = max(1, min(max_frames, round(duration)))
    # Extract serially: one ffmpeg per upload keeps CPU bounded when many
    # users upload at once, which matters more than single-upload latency
    # for an occasional format like video.
    frames: list[SampledFrame] = []
    for i in range(count):
        timestamp = duration * (i + 0.5) / count
        frames.append(
            SampledFrame(
                timestamp=timestamp,
                data=await extract_frame(path, timestamp, max_dimension=max_dimension),
            )
        )
    return MediaSample(frames=tuple(frames), duration=duration)
