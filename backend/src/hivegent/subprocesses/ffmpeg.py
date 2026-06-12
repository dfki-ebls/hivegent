"""Typed async wrappers around ffprobe and ffmpeg for video frame extraction."""

from pathlib import Path
from typing import Any

from .base import run

__all__ = ["extract_frame", "probe_duration"]


async def probe_duration(path: Path) -> float:
    """Return the duration of the media container at *path* in seconds.

    Args:
        path: The media file to probe.

    Returns:
        The container duration in seconds.

    Raises:
        SubprocessError: If ffprobe fails to read the file.
        ValueError: If the container reports no usable duration.
    """
    result = await run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "json",
            path,
        ]
    )
    raw = result.stdout_json(dict[str, Any]).get("format", {}).get("duration")
    try:
        duration = float(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"No usable duration in media file {path.name!r}") from exc
    if duration <= 0:
        raise ValueError(f"Non-positive duration in media file {path.name!r}")
    return duration


async def extract_frame(path: Path, timestamp: float, *, max_dimension: int) -> bytes:
    """Decode the video frame at *timestamp* (seconds) as PNG bytes.

    The frame is scaled down to fit within *max_dimension* on its longer
    side (never scaled up), bounding the payload sent to vision models.

    Args:
        path: The video file to decode.
        timestamp: Seek position in seconds.
        max_dimension: Maximum width/height of the decoded frame.

    Returns:
        PNG-encoded frame bytes.

    Raises:
        SubprocessError: If ffmpeg fails to decode the file.
        ValueError: If no frame exists at *timestamp*.
    """
    scale = (
        f"scale=w='min({max_dimension},iw)':h='min({max_dimension},ih)'"
        ":force_original_aspect_ratio=decrease"
    )
    result = await run(
        [
            "ffmpeg",
            "-v",
            "error",
            # One thread per process: decoding a single seeked frame gains
            # nothing from multithreading, and capping it lets concurrent
            # uploads from different users share cores instead of each ffmpeg
            # trying to grab them all.
            "-threads",
            "1",
            "-ss",
            f"{timestamp:.3f}",
            "-i",
            path,
            "-frames:v",
            "1",
            "-vf",
            scale,
            "-f",
            "image2pipe",
            "-c:v",
            "png",
            "-",
        ]
    )
    if not result.stdout:
        raise ValueError(f"No frame at {timestamp:.3f}s in {path.name!r}")
    return result.stdout
