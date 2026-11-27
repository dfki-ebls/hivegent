"""Tests for the chat request contract and the problems it is refused with."""

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, UserPromptPart

from hivegent.server.problems import PROBLEM_MEDIA_TYPE, install_problem_handlers
from hivegent.types import ChatRequest, DocumentSelection, FilterEntry


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
    """Every setting beside the SDK's body is refused, each by its pointer, since the server keeps them."""
    response = _client().post(
        "/chat",
        json={"id": "c", "messages": [], "included_documents": [], "mode": "read"},
    )

    assert response.status_code == 422
    assert response.headers["content-type"] == PROBLEM_MEDIA_TYPE
    assert {error["pointer"] for error in response.json()["errors"]} == {
        "#/included_documents",
        "#/mode",
    }


def test_the_sdk_body_runs_as_the_adapter_reads_it() -> None:
    """The SDK's own body, camelCase ``messageId`` included, is all a chat request holds."""
    response = _client().post(
        "/chat",
        json={
            "id": "c",
            "messages": [],
            "trigger": "regenerate-message",
            "messageId": "6f1c2b9e-1111-4222-8333-444455556666",
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


def test_a_message_names_the_documents_it_is_about() -> None:
    """The selection rides on the user message, and one that does not validate is refused."""
    message = {
        "id": "6f1c2b9e-2222-4333-8444-555566667777",
        "role": "user",
        "parts": [],
    }
    selection = {"included": [{"path": "~/handbook", "directory": True}]}

    def post(documents: object) -> int:
        body = {
            "id": "c",
            "messages": [{**message, "metadata": {"documents": documents}}],
        }

        return _client().post("/chat", json=body).status_code

    assert post(selection) == 200
    assert post({"included": "bogus"}) == 422


def test_a_turn_runs_with_the_documents_of_its_question() -> None:
    """A regenerate or a compaction answers the last question again, with its selection."""
    asked = ModelRequest(
        parts=[UserPromptPart("q")],
        metadata={"documents": {"excluded": [{"path": "~/secret", "directory": True}]}},
    )
    answer = ModelResponse(parts=[TextPart("a")])

    assert DocumentSelection.of([asked, answer]).excluded == [
        FilterEntry("~/secret", True)
    ]
    assert DocumentSelection.of([answer]) == DocumentSelection()
