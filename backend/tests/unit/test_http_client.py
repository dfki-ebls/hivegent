"""Tests for routing operator endpoints over unix sockets."""

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path

import httpx2
import pytest

from hivegent import security
from hivegent.config import settings
from hivegent.http_client import (
    get_trusted_http_client,
    get_user_http_client,
    shared_http_client_lifespan,
)

URL = "http://llmhop/v1/models"


@pytest.fixture()
async def socket_hits(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[list[bytes]]:
    """Serve HTTP on a unix socket mapped to ``llmhop`` and record each request."""
    hits: list[bytes] = []

    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        hits.append(await reader.readuntil(b"\r\n\r\n"))
        writer.write(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\nok")
        await writer.drain()
        writer.close()

    # A relative path keeps clear of the unix socket path length limit.
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(settings.network, "unix_sockets", {"llmhop": Path("s.sock")})
    server = await asyncio.start_unix_server(handle, path="s.sock")

    async with server:
        yield hits


async def test_trusted_client_reaches_unix_socket(socket_hits: list[bytes]) -> None:
    """A configured socket host is served over its socket, keeping its Host header."""
    async with shared_http_client_lifespan():
        response = await get_trusted_http_client().get(URL)

    assert response.text == "ok"
    assert len(socket_hits) == 1
    assert b"Host: llmhop" in socket_hits[0]


async def test_user_client_ignores_unix_socket(
    socket_hits: list[bytes], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Even an allowlisted socket host goes through the egress proxy for users."""
    proxied: list[str] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        proxied.append(str(request.url))
        return httpx2.Response(502)

    monkeypatch.setattr(settings.security.user_urls, "allow_hosts", ["llmhop"])
    monkeypatch.setattr(
        security, "_egress_transport", lambda _proxy_url: httpx2.MockTransport(handler)
    )

    async with shared_http_client_lifespan():
        await get_user_http_client().get(URL)

    assert proxied == [URL]
    assert socket_hits == []
