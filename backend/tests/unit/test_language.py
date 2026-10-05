"""Tests for serving error details in the request's language."""

import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

from hivegent.config import settings
from hivegent.server.language import LanguageMiddleware
from hivegent.server.routes import transparency


@pytest.mark.parametrize(
    ("accept_language", "detail"),
    [
        ("de-DE,de;q=0.9,en;q=0.8", "Transparenz ist deaktiviert"),
        ("fr", "Transparency is disabled"),
    ],
)
def test_error_detail_follows_accept_language(
    monkeypatch: pytest.MonkeyPatch, accept_language: str, detail: str
) -> None:
    """A German request gets a German detail, an unsupported one English."""
    monkeypatch.setattr(settings, "transparency", None)
    app = FastAPI()
    app.add_middleware(LanguageMiddleware)
    app.include_router(transparency.router)

    response = TestClient(app).post(
        "/transparency/detect",
        json={"text": "x"},
        headers={"Accept-Language": accept_language},
    )

    assert response.status_code == 404
    assert response.json() == {"detail": detail}
