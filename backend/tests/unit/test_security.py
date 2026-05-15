"""Tests for SSRF-sensitive networking helpers."""

import httpx
import pytest

from hivegent.security import create_safe_async_client


async def test_safe_async_client_blocks_private_ip_connections() -> None:
    """The safe transport rejects private addresses at connection time."""
    async with create_safe_async_client(timeout=0.1) as client:
        with pytest.raises(httpx.ConnectError, match="private or reserved"):
            await client.get("http://127.0.0.1:1")
