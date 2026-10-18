"""OpenCV video frame sampling, isolated in a spawned worker.

Run in a throwaway process via :func:`hivegent.workers.isolation.run_isolated`
(driven by :mod:`hivegent.converters.video`), so a crash or hang in the FFmpeg
that OpenCV bundles cannot take the server down on an untrusted upload.
Dependency-light like :mod:`.pdf`: ``cv2`` loads inside the function.
"""

from pathlib import Path

import PIL.Image

from ..imaging import pil_to_still_png

__all__ = ["sample_frames"]


def sample_frames(
    path: Path, max_frames: int, max_dimension: int
) -> tuple[float, tuple[tuple[float, bytes], ...]]:
    """Return the duration of *path* and PNG frames sampled evenly across it.

    Samples roughly one frame per second for short clips, capped at
    *max_frames* for longer material, at segment midpoints so the selection
    spans the whole timeline.

    Raises:
        ValueError: If the video cannot be decoded or reports no usable duration.
    """
    import cv2

    # One decoding thread per video keeps CPU bounded when many users upload
    # at once, which matters more than single-upload latency for an
    # occasional format like video.
    capture = cv2.VideoCapture(str(path), cv2.CAP_FFMPEG, [cv2.CAP_PROP_N_THREADS, 1])
    try:
        fps = capture.get(cv2.CAP_PROP_FPS)
        frame_count = capture.get(cv2.CAP_PROP_FRAME_COUNT)
        if not capture.isOpened() or fps <= 0 or frame_count <= 0:
            raise ValueError(f"No usable duration in video {path.name!r}")

        # The container's frame count over its frame rate, which OpenCV
        # reports without decoding.
        duration = frame_count / fps
        count = max(1, min(max_frames, round(duration)))
        frames: list[tuple[float, bytes]] = []
        for i in range(count):
            timestamp = duration * (i + 0.5) / count
            capture.set(cv2.CAP_PROP_POS_MSEC, timestamp * 1000)
            ok, frame = capture.read()
            if not ok:
                raise ValueError(f"No frame at {timestamp:.3f}s in {path.name!r}")

            image = PIL.Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
            frames.append((timestamp, pil_to_still_png(image, max_dimension)))

        return duration, tuple(frames)
    finally:
        capture.release()
