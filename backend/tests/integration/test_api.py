"""Integration tests for the FastAPI endpoints."""

import json
from pathlib import Path

from hivegent.store import Casebase


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
        "/api/documents/test.md",
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


def test_upload_none_pipeline_stores_as_is(app_client, data_dir: Path) -> None:  # noqa: ANN001
    """PUT with NONE pipeline stores file without conversion."""
    content = b"binary content here"
    spec = {"conversion": {"pipeline": "none"}, "chunking": {"pipeline": "auto"}}
    response = app_client.put(
        "/api/documents/test.bin",
        files={"file": ("test.bin", content, "application/octet-stream")},
        data={"pipeline_spec": json.dumps(spec)},
    )
    assert response.status_code == 200
    upload_data = response.json()
    assert upload_data["filename"] == "test.bin"
    assert upload_data["conversion_pipeline_used"] is None

    # File should be in workspace as-is
    store = Casebase(kind="user", id="localhost")
    workspace = store.workspace_dir(data_dir)
    assert (workspace / "test.bin").read_bytes() == content

    # No originals entry
    originals = store.originals_dir(data_dir)
    assert not (originals / "test.bin").exists()


def test_upload_none_pipeline_chunks_markdown(app_client, data_dir: Path) -> None:  # noqa: ANN001
    """PUT with NONE pipeline still chunks .md files."""
    content = b"# Hello\n\nThis is a test document with enough text."
    spec = {"conversion": {"pipeline": "none"}, "chunking": {"pipeline": "auto"}}
    response = app_client.put(
        "/api/documents/readme.md",
        files={"file": ("readme.md", content, "text/markdown")},
        data={"pipeline_spec": json.dumps(spec)},
    )
    assert response.status_code == 200
    upload_data = response.json()
    assert upload_data["filename"] == "readme.md"
    assert upload_data["chunk_count"] is not None
    assert upload_data["chunk_count"] > 0


def test_get_binary_content_returns_415(app_client, data_dir: Path) -> None:  # noqa: ANN001
    """GET content for a binary file returns 415 Unsupported Media Type."""
    # Upload a binary file with NONE pipeline
    content = b"\x89PNG\r\n\x1a\n\x00\x00"
    spec = {"conversion": {"pipeline": "none"}, "chunking": {"pipeline": "auto"}}
    app_client.put(
        "/api/documents/image.png",
        files={"file": ("image.png", content, "image/png")},
        data={"pipeline_spec": json.dumps(spec)},
    )
    response = app_client.get("/api/documents/image.png")
    assert response.status_code == 415


def test_download_original(app_client, data_dir: Path) -> None:  # noqa: ANN001
    """GET /api/documents/original/ returns the original file."""
    # Seed an original file manually
    store = Casebase(kind="user", id="localhost")
    originals = store.originals_dir(data_dir)
    (originals / "report.pdf").write_bytes(b"%PDF-fake")
    workspace = store.workspace_dir(data_dir)
    (workspace / "report.md").write_text("# Converted report")

    response = app_client.get("/api/documents/original/report.md")
    assert response.status_code == 200
    assert response.content == b"%PDF-fake"


def test_download_original_not_found(app_client, data_dir: Path) -> None:  # noqa: ANN001
    """GET /api/documents/original/ returns 404 when no original exists."""
    store = Casebase(kind="user", id="localhost")
    workspace = store.workspace_dir(data_dir)
    (workspace / "native.md").write_text("# Native markdown")

    response = app_client.get("/api/documents/original/native.md")
    assert response.status_code == 404


def test_create_conversation(app_client) -> None:  # noqa: ANN001
    """POST /api/conversations creates a new conversation."""
    response = app_client.post("/api/conversations")
    assert response.status_code == 200
    data = response.json()
    assert "id" in data
    assert len(data["id"]) > 0
