"""Helpers shared across the test tree.

Fixtures live in ``conftest.py``; this is for the plain functions a test
calls directly.
"""

import io
from collections.abc import Awaitable
from typing import cast

from PIL import Image
from PIL.PngImagePlugin import PngInfo

from hivegent.tools.base import ToolOutput
from hivegent.tools.sink import RedirectedOutput

__all__ = ["png_bytes", "returned"]


def png_bytes(info: PngInfo | None = None) -> bytes:
    """A small PNG, optionally carrying the chunks sanitisation strips."""
    buffer = io.BytesIO()
    Image.new("RGB", (8, 8), (10, 80, 160)).save(buffer, format="PNG", pnginfo=info)

    return buffer.getvalue()


async def returned[T](
    call: Awaitable[ToolOutput[T | RedirectedOutput]],
) -> ToolOutput[T]:
    """Await a tool call that named no ``output_path``, dropping the receipt branch.

    A redirect-capable tool returns a receipt in place of its result when a
    call names an output path, so its return type is a union.  A call that
    names none never takes that branch, and asserting it here once keeps the
    narrowing out of every assertion that follows.
    """
    result = await call
    assert not isinstance(result.data, RedirectedOutput)

    return cast(ToolOutput[T], result)
