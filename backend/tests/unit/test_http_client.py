"""Tests for the direct client of operator endpoints."""

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from hivegent.http_client import get_trusted_http_client, shared_http_client_lifespan
from hivegent.security import OperatorUrl, UnsafeUrlError
from tests.conftest import Configure

ENDPOINT = OperatorUrl("http://llmhop/v1")
URL = "http://llmhop/v1/models"


@pytest.fixture()
async def socket_hits(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, configure: Configure
) -> AsyncIterator[list[bytes]]:
    """Serve HTTP on a unix socket mapped to ``llmhop`` and record each request."""
    hits: list[bytes] = []

    async def handle(
        reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        hits.append(await reader.readuntil(b"\r\n\r\n"))
        writer.write(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\nok")
        await writer.drain()
        writer.close()

    # A relative path keeps clear of the unix socket path length limit.
    monkeypatch.chdir(tmp_path)
    configure(network={"unix_sockets": {"llmhop": Path("s.sock")}})
    server = await asyncio.start_unix_server(handle, path="s.sock")

    async with server:
        yield hits


async def test_trusted_client_reaches_its_socket_and_no_other_origin(
    socket_hits: list[bytes],
) -> None:
    """A configured socket host is served over its socket, keeping its Host header, and nothing else is reached."""
    async with shared_http_client_lifespan():
        client = get_trusted_http_client(ENDPOINT)

        with pytest.raises(UnsafeUrlError):
            _ = await client.get("http://llmhop:8080/")

        assert socket_hits == []
        assert (await client.get(URL)).text == "ok"

    assert len(socket_hits) == 1
    assert b"Host: llmhop" in socket_hits[0]
