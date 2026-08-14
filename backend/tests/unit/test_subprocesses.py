"""Unit tests for async subprocess wrappers."""

from pathlib import Path

import pytest

from hivegent.subprocesses import (
    RgLine,
    SubprocessError,
    jq_filter,
    pandoc_convert,
    rg_search,
    run,
)


class TestRun:
    """Tests for the base ``run`` function."""

    async def test_captures_stdout(self) -> None:
        result = await run(["echo", "hello"])
        assert result.stdout_text.strip() == "hello"
        assert result.returncode == 0

    async def test_captures_stderr(self) -> None:
        result = await run(
            ["sh", "-c", "echo oops >&2; exit 0"],
        )
        assert "oops" in result.stderr_text

    async def test_raises_on_nonzero(self) -> None:
        with pytest.raises(SubprocessError):
            await run(["sh", "-c", "exit 42"])

    async def test_allowed_returncodes(self) -> None:
        result = await run(
            ["sh", "-c", "exit 1"],
            allowed_returncodes=(1,),
        )
        assert result.returncode == 1

    async def test_stdin_pipe(self) -> None:
        result = await run(["cat"], stdin=b"piped input")
        assert result.stdout_text == "piped input"

    async def test_stdout_json(self) -> None:
        result = await run(["echo", '{"key": "value"}'])
        parsed = result.stdout_json(dict)
        assert parsed == {"key": "value"}

    async def test_stdout_ndjson(self) -> None:
        result = await run(
            ["sh", "-c", "echo '{\"a\":1}'\necho '{\"b\":2}'"],
        )
        items = list(result.stdout_ndjson())
        assert items == [{"a": 1}, {"b": 2}]


class TestRgSearch:
    """Tests for ``rg_search``."""

    async def test_finds_matches(self, tmp_path: Path) -> None:
        (tmp_path / "hello.txt").write_text("Hello World\nGoodbye World\n")
        matches = await rg_search("Hello", tmp_path)
        assert len(matches) == 1
        assert matches[0].lines[0].line_number == 1
        assert matches[0].lines[0].is_match
        assert "Hello" in matches[0].lines[0].text

    async def test_no_matches_returns_empty(self, tmp_path: Path) -> None:
        (tmp_path / "hello.txt").write_text("nothing here\n")
        matches = await rg_search("zzz_missing", tmp_path)
        assert matches == []

    async def test_glob_filter(self, tmp_path: Path) -> None:
        (tmp_path / "include.md").write_text("target\n")
        (tmp_path / "exclude.txt").write_text("target\n")
        matches = await rg_search("target", tmp_path, glob="*.md")
        paths = [m.path for m in matches]
        assert any("include.md" in p for p in paths)
        assert not any("exclude.txt" in p for p in paths)

    async def test_decodes_legacy_encoded_lines(self, tmp_path: Path) -> None:
        # ripgrep hands back non-UTF-8 lines base64-encoded under a `bytes`
        # key instead of `text`, in CRLF sources including the terminator.
        (tmp_path / "legacy.txt").write_bytes("Leitfähigkeit\r\n".encode("cp1252"))
        matches = await rg_search("Leitf", tmp_path)
        assert matches[0].lines == (
            RgLine(line_number=1, text="Leitfähigkeit", is_match=True),
        )

    async def test_context_lines(self, tmp_path: Path) -> None:
        (tmp_path / "test.txt").write_text("aaa\nbbb\nccc\nddd\neee\n")
        matches = await rg_search("ccc", tmp_path, context_lines=1)
        assert len(matches) == 1
        assert matches[0].lines == (
            RgLine(line_number=2, text="bbb", is_match=False),
            RgLine(line_number=3, text="ccc", is_match=True),
            RgLine(line_number=4, text="ddd", is_match=False),
        )


class TestJqFilter:
    """Tests for ``jq_filter``."""

    async def test_identity(self) -> None:
        result = await jq_filter(".", {"key": "value"})
        assert result == [{"key": "value"}]

    async def test_field_access(self) -> None:
        result = await jq_filter(".name", {"name": "Alice", "age": 30})
        assert result == ["Alice"]

    async def test_array_iteration(self) -> None:
        result = await jq_filter(".[].x", [{"x": 1}, {"x": 2}])
        assert result == [1, 2]

    async def test_invalid_expression_raises(self) -> None:
        with pytest.raises(ValueError, match="jq failed"):
            await jq_filter("invalid [[[", {"x": 1})

    async def test_empty_input(self) -> None:
        result = await jq_filter(".", [])
        assert result == [[]]


class TestPandocConvert:
    """Tests for ``pandoc_convert``."""

    async def test_html_to_markdown(self, tmp_path: Path) -> None:
        html_file = tmp_path / "test.html"
        html_file.write_text("<h1>Title</h1>\n<p>Body text.</p>\n")
        result = await pandoc_convert(
            html_file,
            from_format="html",
        )
        assert "Title" in result
        assert "Body text" in result

    async def test_plain_text_passthrough(self, tmp_path: Path) -> None:
        txt_file = tmp_path / "test.txt"
        txt_file.write_text("plain text content\n")
        result = await pandoc_convert(
            txt_file,
            from_format="markdown",
        )
        assert "plain text content" in result
