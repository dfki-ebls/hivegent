"""Unit tests for shared tool classes."""

from pathlib import Path

from hivegent.tools import GetDocumentLinesTool, GetDocumentTool, ListDocumentsTool
from hivegent.types import DocumentFilter


class TestListDocumentsTool:
    """Tests for ListDocumentsTool."""

    def test_empty_dir(self, tmp_path: Path) -> None:
        tool = ListDocumentsTool(path=tmp_path)
        assert tool() == []

    def test_lists_md_files(self, tmp_path: Path) -> None:
        (tmp_path / "a.md").write_text("hello")
        (tmp_path / "b.txt").write_text("world")  # not .md, should be ignored
        result = tool = ListDocumentsTool(path=tmp_path)
        result = tool()
        filenames = [r.filename for r in result]
        assert "a.md" in filenames
        assert "b.txt" not in filenames

    def test_subdir_filter(self, tmp_path: Path) -> None:
        sub = tmp_path / "notes"
        sub.mkdir()
        (sub / "n.md").write_text("note")
        (tmp_path / "top.md").write_text("top")
        tool = ListDocumentsTool(path=tmp_path)
        result = tool(subdir="notes")
        filenames = [r.filename for r in result]
        assert "notes/n.md" in filenames
        assert "top.md" not in filenames

    def test_document_filter(self, tmp_path: Path) -> None:
        (tmp_path / "a.md").write_text("a")
        (tmp_path / "b.md").write_text("b")
        doc_filter = DocumentFilter(excluded=frozenset({"b.md"}))
        tool = ListDocumentsTool(path=tmp_path, document_filter=doc_filter)
        result = tool()
        filenames = [r.filename for r in result]
        assert "a.md" in filenames
        assert "b.md" not in filenames

    def test_nonexistent_dir(self, tmp_path: Path) -> None:
        tool = ListDocumentsTool(path=tmp_path / "nonexistent")
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
