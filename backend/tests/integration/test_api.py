"""Integration tests for the FastAPI endpoints."""

from pathlib import Path


def test_get_settings(app_client) -> None:  # noqa: ANN001
    """GET /api/settings returns LLM config and user info."""
    response = app_client.get("/api/settings")
    assert response.status_code == 200
    data = response.json()
    assert "model" in data
    assert "user" in data
    assert data["user"]["id"] == "localhost"


def test_list_documents_empty(app_client) -> None:  # noqa: ANN001
    """GET /api/documents returns empty list initially."""
    response = app_client.get("/api/documents")
    assert response.status_code == 200
    data = response.json()
    assert data["documents"] == []
    assert data["total_count"] == 0


def test_upload_and_list_document(app_client, data_dir: Path) -> None:  # noqa: ANN001
    """PUT a document then GET /api/documents shows it."""
    content = b"# Test Document\n\nHello world."
    response = app_client.put(
        "/api/documents/content/test.md",
        files={"file": ("test.md", content, "text/markdown")},
    )
    assert response.status_code == 200
    upload_data = response.json()
    assert upload_data["filename"] == "test.md"

    # Verify it appears in the list
    response = app_client.get("/api/documents")
    assert response.status_code == 200
    data = response.json()
    filenames = [d["filename"] for d in data["documents"]]
    assert "test.md" in filenames


def test_create_conversation(app_client) -> None:  # noqa: ANN001
    """POST /api/conversation creates a new conversation."""
    response = app_client.post("/api/conversation")
    assert response.status_code == 200
    data = response.json()
    assert "id" in data
    assert len(data["id"]) > 0
