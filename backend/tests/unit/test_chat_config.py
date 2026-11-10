"""Tests for the chat request contract and the problems it is refused with."""

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from hivegent.server.problems import PROBLEM_MEDIA_TYPE, install_problem_handlers
from hivegent.types import ChatRequest


def _client() -> TestClient:
    """An app with one typed chat route and one that refuses, answering as the API does."""
    app = FastAPI()
    install_problem_handlers(app)

    @app.post("/chat")
    async def chat(config: ChatRequest) -> str:
        return config.run_input().trigger

    @app.get("/missing")
    async def missing() -> None:
        raise HTTPException(404, "Conversation not found")

    return TestClient(app)


def test_an_invalid_chat_request_names_every_invalid_member() -> None:
    """A wrong value and a misspelled setting are both refused, each by its pointer."""
    response = _client().post(
        "/chat",
        json={"id": "c", "messages": [], "mode": "bogus", "reasoning": "high"},
    )

    assert response.status_code == 422
    assert response.headers["content-type"] == PROBLEM_MEDIA_TYPE
    assert {error["pointer"] for error in response.json()["errors"]} == {
        "#/mode",
        "#/reasoning",
    }


def test_the_sdk_body_runs_as_the_adapter_reads_it() -> None:
    """The SDK's camelCase ``messageId`` sits beside the snake_case settings."""
    response = _client().post(
        "/chat",
        json={
            "id": "c",
            "messages": [],
            "trigger": "regenerate-message",
            "messageId": "6f1c2b9e-1111-4222-8333-444455556666",
            "reasoning_effort": "low",
        },
    )

    assert response.json() == "regenerate-message"


def test_a_refusal_is_a_problem() -> None:
    """An ``HTTPException`` answers with its status phrase and detail."""
    response = _client().get("/missing")

    assert response.headers["content-type"] == PROBLEM_MEDIA_TYPE
    assert response.json() == {
        "type": "about:blank",
        "title": "Not Found",
        "status": 404,
        "detail": "Conversation not found",
    }
