"""Tests for the SQL query tool over tabular documents."""

from pathlib import Path

import openpyxl
import pytest

from hivegent.tools.base import ToolRetry
from hivegent.tools.table import QueryTableTool


def _sales_dir(tmp_path: Path) -> Path:
    """Write a 50-row table whose EU rows sum to 625 and US rows to 600."""
    rows = "\n".join(f"{'EU' if i % 2 else 'US'},{i}" for i in range(50))
    (tmp_path / "sales.csv").write_text(f"region,amount\n{rows}")

    return tmp_path


def _workbook_dir(tmp_path: Path) -> Path:
    book = openpyxl.Workbook()
    first = book.active
    assert first is not None
    first.title = "Q1"
    first.append(["item", "qty"])
    first.append(["bolt", 7])
    second = book.create_sheet("Q2")
    second.append(["item", "qty"])
    second.append(["nut", 3])
    book.save(tmp_path / "book.xlsx")

    return tmp_path


class TestQueryTableTool:
    """SQL over the original file, so a large table costs a small result."""

    async def test_schema_mode_reports_columns_and_row_count(
        self, tmp_path: Path
    ) -> None:
        tool = QueryTableTool(paths=_sales_dir(tmp_path))
        out = await tool("sales.csv")

        assert out.data.columns == ("region", "amount")
        assert out.data.total_rows == 50
        assert len(out.data.rows) == tool.preview_rows
        assert out.formatted is not None
        assert "amount: Int64" in out.formatted

    async def test_query_aggregates_instead_of_returning_rows(
        self, tmp_path: Path
    ) -> None:
        tool = QueryTableTool(paths=_sales_dir(tmp_path))
        out = await tool(
            "sales.csv",
            "SELECT region, SUM(amount) AS total FROM t GROUP BY region "
            "ORDER BY region",
        )

        assert out.data.rows == (("EU", "625"), ("US", "600"))

    async def test_row_cap_is_named_in_the_output(self, tmp_path: Path) -> None:
        tool = QueryTableTool(paths=_sales_dir(tmp_path), max_rows=10)
        out = await tool("sales.csv", "SELECT * FROM t")

        assert out.data.truncated
        assert len(out.data.rows) == 10
        assert out.formatted is not None
        assert "10 rows shown" in out.formatted

    async def test_legacy_encoding_is_decoded_and_reported(
        self, tmp_path: Path
    ) -> None:
        # A lazy scan only fails once it reaches the offending bytes, so the
        # retry has to survive them sitting anywhere in the file, not just in
        # the header a cheap probe would have seen.
        filler = "\n".join(f"n{i},c{i}" for i in range(200))
        (tmp_path / "legacy.csv").write_bytes(
            f"name,city\n{filler}\nGrüße,Köln\n".encode("cp1252")
        )
        tool = QueryTableTool(paths=tmp_path)
        out = await tool("legacy.csv", "SELECT * FROM t WHERE city = 'Köln'")

        assert out.data.source_encoding == "cp1252"
        assert out.data.rows == (("Grüße", "Köln"),)

    async def test_worksheet_is_selectable_and_the_others_are_named(
        self, tmp_path: Path
    ) -> None:
        tool = QueryTableTool(paths=_workbook_dir(tmp_path))
        out = await tool("book.xlsx", sheet="Q2")

        assert out.data.sheet == "Q2"
        assert out.data.sheets == ("Q1", "Q2")
        assert out.data.rows == (("nut", "3"),)

        with pytest.raises(ToolRetry, match="Q1, Q2"):
            await tool("book.xlsx", sheet="Q9")

    async def test_column_cut_is_named_and_leaves_the_data_channel_whole(
        self, tmp_path: Path
    ) -> None:
        # The cut binds the rendering only: the frontend still gets every
        # column, the way a read keeps its lines untruncated in `content`.
        header = ",".join(f"c{i}" for i in range(12))
        values = ",".join(str(i) for i in range(12))
        (tmp_path / "wide.csv").write_text(f"{header}\n{values}")
        tool = QueryTableTool(paths=tmp_path, max_columns=4)
        out = await tool("wide.csv", "SELECT * FROM t")

        assert len(out.data.columns) == 12
        assert out.formatted is not None
        assert "4 of 12 columns shown" in out.formatted
        assert "c11" not in out.formatted

    async def test_non_tabular_file_is_refused(self, tmp_path: Path) -> None:
        (tmp_path / "notes.md").write_text("plain prose")
        tool = QueryTableTool(paths=tmp_path)

        with pytest.raises(ToolRetry, match="not a tabular file"):
            await tool("notes.md")

    async def test_bad_query_names_the_valid_columns(self, tmp_path: Path) -> None:
        # Polars' own message carries the retry signal; the tool only has to
        # let it through rather than flatten it into "query failed".
        tool = QueryTableTool(paths=_sales_dir(tmp_path))

        with pytest.raises(ToolRetry, match="region"):
            await tool("sales.csv", "SELECT nope FROM t")
