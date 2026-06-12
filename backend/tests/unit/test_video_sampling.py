"""Tests for frame sampling and the binary tool's animated-media support."""

from io import BytesIO
from pathlib import Path

import PIL.Image
import pytest

from hivegent.converters.video import (
    animation_frame_count,
    sample_animated_image,
    sample_video,
)
from hivegent.subprocesses.base import run
from hivegent.tools.base import SearchPath, ToolRetry
from hivegent.tools.binary import ReadBinaryDocumentTool


def _animated_gif(frame_count: int, duration_ms: int = 100) -> bytes:
    frames = [
        PIL.Image.new("RGB", (32, 32), (i * 20 % 256, 0, 0)) for i in range(frame_count)
    ]
    buf = BytesIO()
    frames[0].save(
        buf,
        format="GIF",
        save_all=True,
        append_images=frames[1:],
        duration=duration_ms,
    )
    return buf.getvalue()


def test_frame_count_static_and_animated() -> None:
    assert animation_frame_count(_animated_gif(6), "image/gif") == 6
    static = BytesIO()
    PIL.Image.new("RGB", (32, 32)).save(static, format="PNG")
    assert animation_frame_count(static.getvalue(), "image/png") == 1
    assert animation_frame_count(b"not an image", "image/gif") == 1


def test_sample_animated_image_spans_timeline() -> None:
    sample = sample_animated_image(_animated_gif(12, duration_ms=100), max_frames=4)
    assert len(sample.frames) == 4
    assert sample.duration == pytest.approx(1.2)
    timestamps = [f.timestamp for f in sample.frames]
    assert timestamps == sorted(timestamps)
    assert timestamps[0] < timestamps[-1]
    for frame in sample.frames:
        with PIL.Image.open(BytesIO(frame.data)) as img:
            assert img.format == "PNG"


async def test_sample_video_via_ffmpeg(tmp_path: Path) -> None:
    video = tmp_path / "clip.mp4"
    await run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "testsrc=duration=3:size=64x64:rate=4",
            "-c:v",
            "mpeg4",
            video,
        ]
    )
    sample = await sample_video(video, max_frames=8, max_dimension=48)
    assert sample.duration == pytest.approx(3.0, abs=0.2)
    assert len(sample.frames) == 3
    for frame in sample.frames:
        with PIL.Image.open(BytesIO(frame.data)) as img:
            assert img.format == "PNG"
            assert max(img.size) <= 48


async def test_binary_tool_samples_animated_gif(tmp_path: Path) -> None:
    (tmp_path / "anim.gif").write_bytes(_animated_gif(10))
    tool = ReadBinaryDocumentTool(paths=SearchPath(path=tmp_path), max_frames=4)
    output = await tool("anim.gif")
    assert output.data.frames == 4
    assert output.data.duration == pytest.approx(1.0)
    assert len(output.attachments) == 4
    assert all(a.media_type == "image/png" for a in output.attachments)
    assert "#t=" in (output.attachments[-1].identifier or "")


async def test_binary_tool_rejects_pages_for_video(tmp_path: Path) -> None:
    (tmp_path / "clip.mp4").write_bytes(b"\x00" * 16)
    tool = ReadBinaryDocumentTool(paths=SearchPath(path=tmp_path))
    with pytest.raises(ToolRetry, match="pages="):
        await tool("clip.mp4", pages="1")
