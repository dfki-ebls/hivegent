"""Tests for the SQL query tool over tabular documents."""

from pathlib import Path

import openpyxl
import pytest
from exar.tools import ItemFailure, Retry
from exar.tools.table import TableResult

from hivegent.agents.tools.explore import query_table
from hivegent.tools.base import SearchPath, ToolFunc, mounts_of
from tests.helpers import single

MIXED = "name,val\nr0,0\nr1,1\nr2,N/A\nr3,3"
MIXED_WARNING = 'val (3 of 4 parse as BIGINT; unparsed: "N/A")'


def _tool(root: Path, **limits: int) -> ToolFunc:
    """The tool over the one workspace at *root*."""
    return query_table(mounts_of((SearchPath.of(root),)), **limits)


def _workbook(root: Path, name: str, **sheets: list[list[str | None]]) -> None:
    """Write the workbook *name* whose sheets hold these rows."""
    book = openpyxl.Workbook()
    book.remove(book.active or book.create_sheet())

    for title, rows in sheets.items():
        sheet = book.create_sheet(title)

        for row in rows:
            sheet.append(row)

    book.save(root / name)


class TestQueryTable:
    """SQL over the original file, so a large table costs a small result."""

    async def test_a_schema_describes_the_table_and_a_query_answers_from_it(
        self, tmp_path: Path
    ) -> None:
        rows = "\n".join(f"{'EU' if i % 2 else 'US'},{i}" for i in range(50))
        (tmp_path / "sales.csv").write_text(f"region,amount\n{rows}")
        tool = _tool(tmp_path)

        schema = await tool(["sales.csv"])
        totals = await tool(
            ["sales.csv"],
            ["SELECT region, SUM(amount) AS t FROM t GROUP BY region ORDER BY region"],
        )
        capped = await tool(["sales.csv"], ["SELECT * FROM t"], limit=2)

        assert single(schema.metadata).columns == ("region", "amount")
        assert single(schema.metadata).total_rows == 50
        assert len(single(schema.metadata).rows) == 5
        assert "amount: BIGINT" in schema.return_value
        assert "unparsed" not in schema.return_value
        assert single(totals.metadata).rows == (("EU", "625"), ("US", "600"))
        assert single(capped.metadata).truncated
        assert "2 rows returned, increase limit" in capped.return_value

    async def test_a_worksheet_is_selectable_and_the_others_are_named(
        self, tmp_path: Path
    ) -> None:
        _workbook(
            tmp_path,
            "book.xlsx",
            Q1=[["item", "qty"], ["bolt", "7"]],
            Q2=[["item", "qty"], ["nut", "3"]],
        )
        tool = _tool(tmp_path)

        out = await tool(["book.xlsx"], sheet="Q2")

        assert single(out.metadata).tables[0].sheets == ("Q1", "Q2")
        assert single(out.metadata).rows == (("nut", "3"),)

        with pytest.raises(Retry, match="Q1, Q2"):
            await tool(["book.xlsx"], sheet="Q9")

    async def test_text_columns_are_retyped_when_every_value_parses(
        self, tmp_path: Path
    ) -> None:
        # A spreadsheet hands out numbers and dates as text, which is what
        # otherwise turns a plain SUM or a date comparison into a dtype error.
        _workbook(
            tmp_path,
            "typed.xlsx",
            Sheet=[
                ["zip", "amount", "day"],
                ["01067", "10.5", "2024-01-02"],
                ["10115", "20", "2024-02-03"],
            ],
        )
        tool = _tool(tmp_path)

        out = await tool(
            ["typed.xlsx"], ["SELECT SUM(amount) AS s FROM t WHERE day > '2024-01-15'"]
        )
        schema = await tool(["typed.xlsx"])

        assert single(out.metadata).rows == (("20.0",),)
        # A zero-padded value is an identifier, so the column stays text.
        assert single(schema.metadata).dtypes == ("VARCHAR", "DOUBLE", "DATE")

    @pytest.mark.parametrize(
        ("query", "warned"),
        [
            (None, True),
            ("SELECT name, val FROM t", True),
            ("SELECT * FROM t", True),
            ("SELECT name FROM t", False),
        ],
    )
    async def test_a_mixed_column_is_reported_where_it_is_read(
        self, tmp_path: Path, query: str | None, warned: bool
    ) -> None:
        # One "N/A" keeps the column text. Naming the share that parses and the
        # value that does not is what lets the first query wrap it in TRY_CAST.
        (tmp_path / "mixed.csv").write_text(MIXED)

        out = await _tool(tmp_path)(["mixed.csv"], query and [query])

        assert (MIXED_WARNING in out.return_value) is warned
        assert ("TRY_CAST" in out.return_value) is warned

    async def test_a_query_over_a_mixed_column_is_refused_with_the_cast(
        self, tmp_path: Path
    ) -> None:
        (tmp_path / "mixed.csv").write_text(MIXED)

        with pytest.raises(Retry, match="TRY_CAST"):
            await _tool(tmp_path)(["mixed.csv"], ["SELECT SUM(val) FROM t"])

    async def test_an_unparsable_value_is_named_and_handed_back(
        self, tmp_path: Path
    ) -> None:
        # `<100` is a lab declining to put a number on a sample it measured,
        # and every substitution for one moves every total, so the user is asked.
        (tmp_path / "lab.csv").write_text("date,csb\n2023-01-01,153\n2023-01-02,<100")

        out = await _tool(tmp_path)(["lab.csv"], ["SELECT date, csb FROM t"])

        assert single(out.metadata).text_columns[0].unparsed == ("<100",)
        assert "ask the user" in out.return_value

    async def test_the_worst_shortfall_leads_and_the_cap_is_named(
        self, tmp_path: Path
    ) -> None:
        header = ",".join(f"c{i}" for i in range(5))
        rows = "\n".join(
            ",".join("x" if i <= column else str(i) for column in range(5))
            for i in range(5)
        )
        (tmp_path / "wide.csv").write_text(f"{header}\n{rows}")

        out = await _tool(tmp_path, max_named_columns=2)(["wide.csv"])

        mixed = out.return_value.split("mixed text, wrap in TRY_CAST to compare: ")[1]
        assert mixed.startswith("c3 (1 of 5")
        assert "and 2 more columns" in mixed

    async def test_a_cell_clip_is_named_and_leaves_the_data_channel_whole(
        self, tmp_path: Path
    ) -> None:
        (tmp_path / "wide.csv").write_text(f"note\n{'x' * 30}end")
        tool = _tool(tmp_path, max_cell_chars=10)

        out = await tool(["wide.csv"], ["SELECT * FROM t"])
        whole = await tool(["wide.csv"], ["SELECT * FROM t"], full_lines=True)

        assert single(out.metadata).rows == (("x" * 30 + "end",),)
        assert "cells clipped at 10 chars" in out.return_value
        assert "end" not in out.return_value
        assert "x" * 30 + "end" in whole.return_value
        assert "clipped" not in whole.return_value

    @pytest.mark.parametrize(
        ("name", "query", "message"),
        [
            ("notes.md", None, "not a tabular file"),
            ("sales.csv", "SELECT nope FROM t", "region"),
            ("sales.csv", "SELECT * FROM read_csv('sales.csv')", "file system"),
            ("sales.csv", "SELECT * FROM \"READ_JSON\"('x.json')", "file system"),
        ],
        ids=["not-tabular", "bad-column", "read-csv", "read-json"],
    )
    async def test_a_call_that_cannot_run_is_a_retry_naming_the_correction(
        self, tmp_path: Path, name: str, query: str | None, message: str
    ) -> None:
        (tmp_path / "notes.md").write_text("plain prose")
        (tmp_path / "sales.csv").write_text("region,amount\nEU,1")

        with pytest.raises(Retry, match=message):
            await _tool(tmp_path)([name], query and [query])


class TestMultipleTables:
    """A query spans every file it was given, so a join needs only the SQL."""

    async def test_tables_join_under_positional_names_and_each_query_stands_alone(
        self, tmp_path: Path
    ) -> None:
        (tmp_path / "sales.csv").write_text("id,amount\n1,10\n2,20\n")
        (tmp_path / "regions.csv").write_text("id,region\n1,EU\n2,US\n")
        tool = _tool(tmp_path)
        files = ["sales.csv", "regions.csv"]
        join = (
            "SELECT t2.region, SUM(t.amount) AS total FROM t "
            "JOIN t2 ON t.id = t2.id GROUP BY t2.region ORDER BY t2.region"
        )

        joined = await tool(files, [join])
        mixed = await tool(
            files, ["SELECT COUNT(*) AS n FROM t2", "SELECT nope FROM t"]
        )

        assert single(joined.metadata).rows == (("EU", "10"), ("US", "20"))
        # A join cannot be written without knowing which file took which name.
        assert "t: sales.csv" in joined.return_value
        assert "t2: regions.csv" in joined.return_value
        counted, failed = mixed.metadata
        assert isinstance(counted, TableResult)
        assert counted.rows == (("2",),)
        assert isinstance(failed, ItemFailure)
        assert failed.item == "SELECT nope FROM t"

    async def test_a_mixed_column_is_qualified_by_its_table(
        self, tmp_path: Path
    ) -> None:
        (tmp_path / "a.csv").write_text("val\n1\n")
        (tmp_path / "b.csv").write_text("val\n1\nN/A\n")

        out = await _tool(tmp_path)(["a.csv", "b.csv"], ["SELECT t2.val FROM t2"])

        assert 't2.val (1 of 2 parse as BIGINT; unparsed: "N/A")' in out.return_value


class TestColumnSpelling:
    """The schema listing is the only place a model sees a column name."""

    async def test_a_name_that_is_not_an_identifier_is_listed_and_run_quoted(
        self, tmp_path: Path
    ) -> None:
        (tmp_path / "t.csv").write_text("Zulaufmenge (D),amount\n1,2\n")
        tool = _tool(tmp_path)

        schema = await tool(["t.csv"])
        quoted = await tool(["t.csv"], ['SELECT "Zulaufmenge (D)" FROM t'])

        assert '"Zulaufmenge (D)": BIGINT' in schema.return_value
        assert single(quoted.metadata).rows == (("1",),)

        with pytest.raises(Retry, match="double-quoted") as exc:
            await tool(["t.csv"], ["SELECT Zulaufmenge (D) FROM t"])

        assert '"Zulaufmenge (D)"' in str(exc.value)

    async def test_a_spilled_header_row_shows_up_as_its_own_labels(
        self, tmp_path: Path
    ) -> None:
        # One label row holds every numeric column of a sheet as text. Only the
        # sheet's author knows whether row 1 is labels, but naming the value in
        # each column makes it visible.
        _workbook(
            tmp_path,
            "export.xlsx",
            Sheet=[
                ["Datum", None, "Zulaufmenge (D)"],
                ["Einstellung", "Meiches", "Zaehlwert"],
                ["2023-01-01", "0.4", "9362"],
            ],
        )

        out = await _tool(tmp_path)(["export.xlsx"])

        assert 'unparsed: "Einstellung"' in out.return_value
        assert 'unparsed: "Zaehlwert"' in out.return_value

    async def test_header_rows_are_guessed_and_skipped_on_request(
        self, tmp_path: Path
    ) -> None:
        # A group row above the names and a units row below them, as exported.
        (tmp_path / "grouped.csv").write_text(
            ",Gruppe A,\nName,Menge (kg),Preis\n,kg,EUR\nx,1.5,2\ny,3,4\n"
        )
        tool = _tool(tmp_path)

        guess = single((await tool(["grouped.csv"])).metadata).tables[0].header_guess
        assert guess is not None
        assert (guess.skip_above_header, guess.skip_below_header) == (1, 1)
        assert guess.names == ("Name", "Menge (kg)", "Preis")

        out = await tool(
            ["grouped.csv"],
            ['SELECT SUM("Menge (kg)") AS total FROM t'],
            skip_above_header=1,
            skip_below_header=1,
        )

        assert single(out.metadata).rows == (("4.5",),)
