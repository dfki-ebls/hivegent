"""Tests for chat request configuration validation."""

import json
from typing import Any

import pytest
from pydantic import ValidationError
from starlette.requests import Request

from hivegent.server.app import validation_error_handler
from hivegent.server.routes.conversations import _run_chat
from hivegent.types import User


def _request(body: dict[str, Any]) -> Request:
    """Build a Starlette request carrying one JSON body."""
    chunks = [
        {
            "type": "http.request",
            "body": json.dumps(body).encode(),
            "more_body": False,
        }
    ]

    async def receive() -> dict[str, Any]:
        return chunks.pop() if chunks else {"type": "http.disconnect"}

    return Request(
        {"type": "http", "method": "POST", "path": "/", "headers": []},
        receive,
    )


@pytest.mark.parametrize(
    "body",
    [
        {"mode": "bogus"},
        {"reasoning_effort": "impossible"},
    ],
)
async def test_invalid_chat_configuration_returns_422(body: dict[str, Any]) -> None:
    """The route validates the config; the app-wide handler answers 422."""
    request = _request(body)

    with pytest.raises(ValidationError) as raised:
        await _run_chat("conversation", request, User(id="user"))

    response = await validation_error_handler(request, raised.value)

    assert response.status_code == 422
