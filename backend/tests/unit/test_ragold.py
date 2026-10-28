"""Tests for the RAGold export of a conversation turn."""

import hashlib
import io
import json
from pathlib import Path
from zipfile import ZipFile

import jsonschema
import pytest
from fastapi import HTTPException
from pydantic import HttpUrl

from hivegent.chunkers.base import EntryMetadata
from hivegent.config import settings
from hivegent.db import documents as db_documents
from hivegent.ragold import RagoldRequest, plain_text
from hivegent.server.routes.conversations import export_ragold_route
from hivegent.store import Casebase, User

from ..helpers import owned

SCHEMA = Path(__file__).parents[1] / "fixtures" / "ragold-schema.json"


@pytest.fixture(autouse=True)
def _ragold(monkeypatch: pytest.MonkeyPatch, data_dir: Path) -> None:
    """Enable the export over a workspace that has no database rows."""

    async def no_metadata(_store: Casebase, _reference: str) -> EntryMetadata | None:
        return None

    _ = data_dir
    monkeypatch.setattr(settings.ragold, "url", HttpUrl("https://ragold.test"))
    monkeypatch.setattr(db_documents, "get_entry_metadata", no_metadata)


def _request(**changes: object) -> RagoldRequest:
    return RagoldRequest.model_validate(
        {
            "query": "What is the report about?",
            "response": "Bees.",
            "questionType": "simple",
            "answerability": "answerable",
            "origin": "https://hivegent.test/conversations/c1",
            "passages": [
                {"path": "~/report.md", "text": "**Bees**", "grade": "needed"}
            ],
            "documents": ["~/report.md", "~/notes.md"],
        }
        | changes
    )


async def test_archive_matches_the_ragold_schema() -> None:
    """The zip ships originals, markdown-only entries, and a schema-valid envelope."""
    user = User("u", name="Ada")
    workspace = user.store.workspace_dir(settings.data_dir)
    original = b"%PDF-1.7 bees"
    (workspace / "report.pdf").write_bytes(original)
    (workspace / "report.md").write_text("Bees")
    (workspace / "notes.md").write_text("Notes")

    response = await export_ragold_route(owned("c1", user), _request(), settings.ragold)
    body = io.BytesIO()

    async for chunk in response.body_iterator:
        assert isinstance(chunk, bytes)
        body.write(chunk)

    with ZipFile(body) as archive:
        data = json.loads(archive.read("annotations.json"))
        jsonschema.validate(data, json.loads(SCHEMA.read_text()))
        documents = data["documents"]
        (document_id,) = (
            key
            for key, document in documents.items()
            if document["name"] == "report.pdf"
        )

        assert archive.read(f"files/{document_id}/report.pdf") == original
        assert documents[document_id]["sha256"] == hashlib.sha256(original).hexdigest()
        assert {document["name"] for document in documents.values()} == {
            "report.pdf",
            "notes.md",
        }
        (annotation,) = data["annotations"].values()
        assert annotation["passages"] == [
            {"documentId": document_id, "text": "Bees", "location": "", "grade": "needed"}
        ]
        assert data["author"] == "Ada"


async def test_refuses_unreachable_and_unlisted_documents() -> None:
    """A group the caller is not in is refused, and passages must name a shipped document."""
    with pytest.raises(HTTPException) as refused:
        await export_ragold_route(
            owned("c1"),
            _request(passages=[], documents=["@secret/report.md"]),
            settings.ragold,
        )

    assert refused.value.status_code == 403

    with pytest.raises(ValueError, match="unlisted"):
        _request(documents=["~/notes.md"])

    with pytest.raises(ValueError, match="markdown syntax"):
        _request(passages=[{"path": "~/report.md", "text": "---", "grade": "needed"}])


@pytest.mark.parametrize(
    ("markdown", "text"),
    [
        ("Some *bees* and __wasps__", "Some bees and wasps"),
        ("## Hive\n\nQueen", "Hive\nQueen"),
        ("| a | b |\n| :- | -: |\n| 1 | 2 |", "a\tb\n1\t2"),
        ("- one\n- [two](https://two.test) `three`", "one\ntwo three"),
        ("```py\nx = **y**\n\nz\n```", "x = **y**\n\nz"),
        ("<b>A</b><br>B \\* \\_c\\_", "A\nB * _c_"),
        ("old** news\n|---|---|\n| 3 | 4 |", "old** news\n3\t4"),
    ],
)
def test_plain_text(markdown: str, text: str) -> None:
    """Markdown syntax is dropped, cut-off constructs degrade to their text."""
    assert plain_text(markdown) == text
