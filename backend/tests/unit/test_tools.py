"""Unit tests for shared tool classes and ToolFactory."""

import json
from pathlib import Path

from hivegent.tools import (
    EditDocumentTool,
    GetChunkTool,
    GetDocumentLinesTool,
    GetDocumentTool,
    GlobDocumentsTool,
    JqTool,
    ListChunksTool,
    ListDocumentsTool,
    SearchTool,
    WriteDocumentTool,
)
from hivegent.types import ChunkSummary, DocumentFilter, RetrievedChunk


class TestListDocumentsTool:
    """Tests for ListDocumentsTool."""

    def test_empty_dir(self, tmp_path: Path) -> None:
        tool = ListDocumentsTool(path=tmp_path, extension=".md")
        assert tool() == []

    def test_lists_md_files(self, tmp_path: Path) -> None:
        (tmp_path / "a.md").write_text("hello")
        (tmp_path / "b.txt").write_text("world")  # not .md, should be ignored
        tool = ListDocumentsTool(path=tmp_path, extension=".md")
        result = tool()
        filenames = [r.filename for r in result]
        assert "a.md" in filenames
        assert "b.txt" not in filenames

    def test_custom_extension(self, tmp_path: Path) -> None:
        (tmp_path / "a.txt").write_text("hello")
        (tmp_path / "b.md").write_text("world")
        tool = ListDocumentsTool(path=tmp_path, extension=".txt")
        result = tool()
        filenames = [r.filename for r in result]
        assert "a.txt" in filenames
        assert "b.md" not in filenames

    def test_subdir_filter(self, tmp_path: Path) -> None:
        sub = tmp_path / "notes"
        sub.mkdir()
        (sub / "n.md").write_text("note")
        (tmp_path / "top.md").write_text("top")
        tool = ListDocumentsTool(path=tmp_path, extension=".md")
        result = tool(subdir="notes")
        filenames = [r.filename for r in result]
        assert "notes/n.md" in filenames
        assert "top.md" not in filenames

    def test_document_filter(self, tmp_path: Path) -> None:
        (tmp_path / "a.md").write_text("a")
        (tmp_path / "b.md").write_text("b")
        doc_filter = DocumentFilter(excluded=frozenset({"b.md"}))
        tool = ListDocumentsTool(
            path=tmp_path, extension=".md", document_filter=doc_filter
        )
        result = tool()
        filenames = [r.filename for r in result]
        assert "a.md" in filenames
        assert "b.md" not in filenames

    def test_nonexistent_dir(self, tmp_path: Path) -> None:
        tool = ListDocumentsTool(path=tmp_path / "nonexistent", extension=".md")
        assert tool() == []


class TestGetDocumentTool:
    """Tests for GetDocumentTool."""

    def test_reads_file(self, tmp_path: Path) -> None:
        (tmp_path / "doc.md").write_text("content here")
        tool = GetDocumentTool(path=tmp_path)
        assert tool("doc.md") == "content here"

    def test_returns_none_for_nonexistent(self, tmp_path: Path) -> None:
        tool = GetDocumentTool(path=tmp_path)
        assert tool("missing.md") is None

    def test_returns_none_for_filtered(self, tmp_path: Path) -> None:
        (tmp_path / "secret.md").write_text("secret")
        doc_filter = DocumentFilter(excluded=frozenset({"secret.md"}))
        tool = GetDocumentTool(path=tmp_path, document_filter=doc_filter)
        assert tool("secret.md") is None

    def test_rejects_path_traversal(self, tmp_path: Path) -> None:
        tool = GetDocumentTool(path=tmp_path)
        assert tool("../../../etc/passwd") is None


class TestGetDocumentLinesTool:
    """Tests for GetDocumentLinesTool."""

    def test_correct_range(self, tmp_path: Path) -> None:
        lines = ["line1", "line2", "line3", "line4", "line5"]
        (tmp_path / "doc.md").write_text("\n".join(lines))
        tool = GetDocumentLinesTool(path=tmp_path)
        result = tool("doc.md", start=2, end=4)
        assert result is not None
        assert result.start_line == 2
        assert result.end_line == 4
        assert result.total_lines == 5
        assert result.content == "line2\nline3\nline4"

    def test_defaults_to_full_file(self, tmp_path: Path) -> None:
        (tmp_path / "doc.md").write_text("a\nb\nc")
        tool = GetDocumentLinesTool(path=tmp_path)
        result = tool("doc.md")
        assert result is not None
        assert result.start_line == 1
        assert result.end_line == 3

    def test_returns_none_for_nonexistent(self, tmp_path: Path) -> None:
        tool = GetDocumentLinesTool(path=tmp_path)
        assert tool("missing.md") is None

    def test_clamps_start_to_one(self, tmp_path: Path) -> None:
        (tmp_path / "doc.md").write_text("only")
        tool = GetDocumentLinesTool(path=tmp_path)
        result = tool("doc.md", start=-5)
        assert result is not None
        assert result.start_line == 1


class TestGlobDocumentsTool:
    """Tests for GlobDocumentsTool."""

    def test_matches_pattern(self, tmp_path: Path) -> None:
        (tmp_path / "notes.md").write_text("a")
        (tmp_path / "readme.md").write_text("b")
        tool = GlobDocumentsTool(path=tmp_path, extension=".md")
        result = tool("note*")
        assert result == ["notes.md"]

    def test_custom_extension(self, tmp_path: Path) -> None:
        (tmp_path / "data.txt").write_text("a")
        (tmp_path / "data.md").write_text("b")
        tool = GlobDocumentsTool(path=tmp_path, extension=".txt")
        result = tool("*")
        assert result == ["data.txt"]


class TestSearchTool:
    """Tests for SearchTool."""

    def test_delegates_to_search_fn(self) -> None:
        expected = [
            RetrievedChunk(
                store_key="user:test",
                filename="doc.md",
                chunk_index=0,
                text="hello",
                token_count=1,
                score=0.9,
            )
        ]

        def mock_search(query: str, top_k: int) -> list[RetrievedChunk]:
            return expected

        tool = SearchTool(search_fn=mock_search)
        assert tool("hello", 5) == expected


class TestListChunksTool:
    """Tests for ListChunksTool."""

    def test_returns_chunks(self) -> None:
        chunks = [ChunkSummary(token_count=10, start_index=0, end_index=50)]

        def loader(filename: str) -> list[ChunkSummary] | None:
            if filename == "doc.md":
                return chunks
            return None

        tool = ListChunksTool(loader=loader)
        assert tool("doc.md") == chunks

    def test_returns_none_for_missing(self) -> None:
        tool = ListChunksTool(loader=lambda _: None)
        assert tool("missing.md") is None

    def test_respects_filter(self) -> None:
        doc_filter = DocumentFilter(excluded=frozenset({"secret.md"}))
        tool = ListChunksTool(
            loader=lambda _: [ChunkSummary(token_count=1, start_index=0, end_index=1)],
            document_filter=doc_filter,
        )
        assert tool("secret.md") is None


class TestGetChunkTool:
    """Tests for GetChunkTool."""

    def test_returns_chunk_text(self) -> None:
        def loader(filename: str, chunk_index: int) -> str | None:
            if filename == "doc.md" and chunk_index == 0:
                return "chunk content"
            return None

        tool = GetChunkTool(loader=loader)
        assert tool("doc.md", 0) == "chunk content"

    def test_returns_none_for_invalid_index(self) -> None:
        tool = GetChunkTool(loader=lambda _f, _i: None)
        assert tool("doc.md", 99) is None

    def test_respects_filter(self) -> None:
        doc_filter = DocumentFilter(excluded=frozenset({"secret.md"}))
        tool = GetChunkTool(
            loader=lambda _f, _i: "text",
            document_filter=doc_filter,
        )
        assert tool("secret.md", 0) is None


class TestEditDocumentTool:
    """Tests for EditDocumentTool."""

    async def test_replaces_string(self, tmp_path: Path) -> None:
        (tmp_path / "doc.md").write_text("hello world")
        tool = EditDocumentTool(path=tmp_path)
        result = await tool("doc.md", "hello", "goodbye")
        assert "Replaced 1 occurrence" in result
        assert (tmp_path / "doc.md").read_text() == "goodbye world"

    async def test_calls_on_write(self, tmp_path: Path) -> None:
        (tmp_path / "doc.md").write_text("hello world")
        written: list[str] = []

        async def _on_write(filename: str) -> None:
            written.append(filename)

        tool = EditDocumentTool(path=tmp_path, on_write=_on_write)
        await tool("doc.md", "hello", "goodbye")
        assert written == ["doc.md"]

    async def test_error_on_missing_string(self, tmp_path: Path) -> None:
        (tmp_path / "doc.md").write_text("hello world")
        tool = EditDocumentTool(path=tmp_path)
        result = await tool("doc.md", "missing", "new")
        assert "Error" in result

    async def test_error_on_duplicate_string(self, tmp_path: Path) -> None:
        (tmp_path / "doc.md").write_text("hello hello")
        tool = EditDocumentTool(path=tmp_path)
        result = await tool("doc.md", "hello", "goodbye")
        assert "Error" in result
        assert "2 times" in result


class TestWriteDocumentTool:
    """Tests for WriteDocumentTool."""

    async def test_replace_creates_file(self, tmp_path: Path) -> None:
        tool = WriteDocumentTool(path=tmp_path, extension=".md")
        result = await tool("new.md", "content")
        assert "Wrote" in result
        assert (tmp_path / "new.md").read_text() == "content"

    async def test_append(self, tmp_path: Path) -> None:
        (tmp_path / "doc.md").write_text("start")
        tool = WriteDocumentTool(path=tmp_path, extension=".md")
        result = await tool("doc.md", " end", mode="append")
        assert "Appended" in result
        assert (tmp_path / "doc.md").read_text() == "start end"

    async def test_prepend(self, tmp_path: Path) -> None:
        (tmp_path / "doc.md").write_text("end")
        tool = WriteDocumentTool(path=tmp_path, extension=".md")
        result = await tool("doc.md", "start ", mode="prepend")
        assert "Prepended" in result
        assert (tmp_path / "doc.md").read_text() == "start end"

    async def test_rejects_wrong_extension(self, tmp_path: Path) -> None:
        tool = WriteDocumentTool(path=tmp_path, extension=".md")
        result = await tool("doc.txt", "content")
        assert "Error" in result

    async def test_calls_on_write(self, tmp_path: Path) -> None:
        written: list[str] = []

        async def _on_write(filename: str) -> None:
            written.append(filename)

        tool = WriteDocumentTool(
            path=tmp_path, extension=".md", on_write=_on_write
        )
        await tool("doc.md", "content")
        assert written == ["doc.md"]


class TestJqTool:
    """Tests for JqTool."""

    async def test_single_file_query(self, tmp_path: Path) -> None:
        data = {"title": "Hello", "count": 42}
        (tmp_path / "item.json").write_text(json.dumps(data))
        tool = JqTool(path=tmp_path)
        result = json.loads(await tool(".title", "item.json"))
        assert result == ["Hello"]

    async def test_all_files_query_with_id_injection(self, tmp_path: Path) -> None:
        (tmp_path / "alpha.json").write_text(json.dumps({"val": 1}))
        (tmp_path / "beta.json").write_text(json.dumps({"val": 2}))
        tool = JqTool(path=tmp_path)
        raw = json.loads(await tool("[.[] | {id, val}]"))
        result = raw[0]
        ids = {item["id"] for item in result}
        assert ids == {"alpha", "beta"}
        vals = {item["val"] for item in result}
        assert vals == {1, 2}

    async def test_invalid_jq_expression(self, tmp_path: Path) -> None:
        (tmp_path / "item.json").write_text(json.dumps({"x": 1}))
        tool = JqTool(path=tmp_path)
        result = await tool("invalid [[[", "item.json")
        assert result.startswith("Error:")

    async def test_empty_directory(self, tmp_path: Path) -> None:
        tool = JqTool(path=tmp_path)
        result = json.loads(await tool("."))
        assert result == [[]]

    async def test_nonexistent_filename(self, tmp_path: Path) -> None:
        tool = JqTool(path=tmp_path)
        result = await tool(".", "missing.json")
        assert result.startswith("Error:")

    async def test_nonexistent_directory(self, tmp_path: Path) -> None:
        tool = JqTool(path=tmp_path / "nonexistent")
        result = await tool(".")
        assert result == "[]"
