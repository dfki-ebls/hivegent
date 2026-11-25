"""Tests for the binary tool's animated-media support."""

from io import BytesIO
from pathlib import Path

import PIL.Image
import pytest
from exar.tools import Retry
from exar.tools.batch import unpack
from exar.tools.media import BinaryRead
from pydantic_ai import BinaryContent

from hivegent.agents.tools.explore import read_binary_document
from hivegent.tools.base import SearchPath, mounts_of
from tests.helpers import single


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


async def test_binary_tool_samples_animated_gif(tmp_path: Path) -> None:
    (tmp_path / "anim.gif").write_bytes(_animated_gif(10))
    tool = read_binary_document(
        mounts_of((SearchPath.of(path=tmp_path),)), max_frames=4
    )
    output = await tool([BinaryRead("anim.gif")])
    files = [a for a in unpack(output).files if isinstance(a, BinaryContent)]
    assert single(output.metadata).frames == 4
    assert single(output.metadata).duration == pytest.approx(1.0)
    assert len(files) == 4
    assert all(a.media_type == "image/png" for a in files)
    assert "#t=" in files[-1].identifier


async def test_binary_tool_clamps_frames_to_the_image_cap(tmp_path: Path) -> None:
    # A run picks PDF pages but never frames, so the serving gateway's
    # per-request image cap shrinks the sample instead of refusing the call.
    (tmp_path / "anim.gif").write_bytes(_animated_gif(10))
    tool = read_binary_document(
        mounts_of((SearchPath.of(path=tmp_path),)), max_frames=4, max_images=2
    )
    output = await tool([BinaryRead("anim.gif")])
    assert single(output.metadata).frames == 2
    assert len(unpack(output).files) == 2


async def test_binary_tool_rejects_pages_for_video(tmp_path: Path) -> None:
    (tmp_path / "clip.mp4").write_bytes(b"\x00" * 16)
    tool = read_binary_document(mounts_of((SearchPath.of(path=tmp_path),)))
    with pytest.raises(Retry, match="pages="):
        await tool([BinaryRead("clip.mp4", pages="1")])
