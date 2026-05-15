"""Integration tests for the FastAPI endpoints."""

from pathlib import Path

from hivegent.store import Casebase


def test_get_settings(app_client) -> None:
    """GET /api/settings returns LLM config and user info."""
    response = app_client.get("/api/settings")
    assert response.status_code == 200
    data = response.json()
    assert "model" in data
    assert "user" in data
    assert data["user"]["id"] == "localhost"


def test_list_documents_empty(app_client) -> None:
    """GET /api/documents returns empty list initially."""
    response = app_client.get("/api/documents")
    assert response.status_code == 200
    data = response.json()
    assert data["documents"] == []
    assert data["total_count"] == 0


def test_upload_and_list_document(app_client, data_dir: Path) -> None:
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
    listed = next(d for d in data["documents"] if d["filename"] == "test.md")
    assert listed["display_name"] == "test"


def test_download_original(app_client, data_dir: Path) -> None:
    """GET /api/documents/original/ returns the original file."""
    store = Casebase(kind="user", id="localhost")
    workspace = store.workspace_dir(data_dir)
    (workspace / "report.pdf").write_bytes(b"%PDF-fake")
    (workspace / "report.md").write_text("# Converted report")

    response = app_client.get("/api/documents/original/report.md")
    assert response.status_code == 200
    assert response.content == b"%PDF-fake"


def test_download_original_not_found(app_client, data_dir: Path) -> None:
    """GET /api/documents/original/ returns 404 when no original exists."""
    store = Casebase(kind="user", id="localhost")
    workspace = store.workspace_dir(data_dir)
    (workspace / "native.md").write_text("# Native markdown")

    response = app_client.get("/api/documents/original/native.md")
    assert response.status_code == 404


def test_download_original_uses_safe_content_disposition(
    app_client,
    data_dir: Path,
) -> None:
    """GET /api/documents/original/ sanitizes hostile filenames in headers."""
    store = Casebase(kind="user", id="localhost")
    workspace = store.workspace_dir(data_dir)
    original = workspace / 'bad"name.pdf'
    original.write_bytes(b"%PDF-fake")
    (workspace / 'bad"name.md').write_text("# Converted report")

    response = app_client.get('/api/documents/original/bad"name.md')

    assert response.status_code == 200
    assert response.headers["content-disposition"].startswith(
        'attachment; filename="bad_name.pdf"'
    )
    assert 'filename*=UTF-8\'\'bad%22name.pdf' in response.headers[
        "content-disposition"
    ]


def test_replace_original_route_is_not_captured_by_upload(
    app_client,
    data_dir: Path,
) -> None:
    """PUT /api/documents/original/ targets original replacement, not document upload."""
    store = Casebase(kind="user", id="localhost")
    workspace = store.workspace_dir(data_dir)
    (workspace / "report.md").write_text("# Converted report")

    response = app_client.put(
        "/api/documents/original/report.md",
        files={"file": ("report.pdf", b"%PDF-updated", "application/pdf")},
    )

    assert response.status_code == 404
    assert response.json()["detail"] == "No original file found for 'report.md'"
    assert not (workspace / "report.pdf").exists()


def test_upload_image_creates_original_and_description(
    app_client,
    data_dir: Path,
) -> None:
    """PUT an image creates the original file and a markdown description."""
    png_bytes = (
        b"\x89PNG\r\n\x1a\n"
        b"\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
        b"\x08\x02\x00\x00\x00\x90wS\xde"
        b"\x00\x00\x00\x0cIDATx\x9cc\xf8\xcf\xc0\x00\x00\x03\x01\x01"
        b"\x00\xc9\xfe\x92\xef\x00\x00\x00\x00IEND\xaeB`\x82"
    )
    response = app_client.put(
        "/api/documents/diagram.png",
        files={"file": ("diagram.png", png_bytes, "image/png")},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["filename"] == "diagram.png"
    assert data["converted_filename"] == "diagram.md"
    assert data["chunk_count"] == 1
    assert data["chunking_pipeline_used"] == "none"

    store = Casebase(kind="user", id="localhost")
    workspace = store.workspace_dir(data_dir)
    assert (workspace / "diagram.png").exists()
    assert (workspace / "diagram.md").exists()

    meta = (store.metadata_dir(data_dir) / "diagram.json").read_text(encoding="utf-8")
    assert '"original_path": "diagram.png"' in meta
    assert '"entry_kind": "image"' in meta


def test_patch_asset_description_rejects_path_traversal(
    app_client,
    data_dir: Path,
) -> None:
    """PATCH asset descriptions rejects names outside the assets directory."""
    store = Casebase(kind="user", id="localhost")
    workspace = store.workspace_dir(data_dir)
    assets = workspace / "diagram.assets"
    assets.mkdir()
    (assets / "image.png").write_bytes(b"png")
    (workspace / "outside.png").write_bytes(b"outside")

    response = app_client.patch(
        "/api/documents/assets/diagram.md",
        json={"asset_name": "../outside.png", "content": "owned"},
    )

    assert response.status_code == 400
    assert not (workspace / "outside.md").exists()


def test_create_conversation(app_client) -> None:
    """POST /api/conversations creates a new conversation."""
    response = app_client.post("/api/conversations")
    assert response.status_code == 200
    data = response.json()
    assert "id" in data
    assert len(data["id"]) > 0
