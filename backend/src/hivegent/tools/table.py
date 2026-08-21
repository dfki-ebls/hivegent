"""Query tabular documents with SQL instead of reading projected rows."""

import asyncio
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Annotated, override

import fastexcel
import polars as pl
from pydantic import Field

from ..converters import TABULAR_SUFFIXES, is_tabular
from ..humanize import pluralize
from .base import (
    AsyncPathTool,
    ToolOutput,
    ToolRetry,
    read_text_or_retry,
    resolve_file_or_retry,
    sidecar_hint,
)
from .documents import DocumentFilePathArg
from .formatting import cap_lines, hint_suffix, truncate_line

__all__ = [
    "QueryTableTool",
    "TableQueryArg",
    "TableResult",
    "TableSheetArg",
]

_DELIMITED_SUFFIXES = frozenset({".csv", ".tsv"})
"""Formats that can be retried through the shared text decoder."""

_RELATION = "t"
"""Fixed query relation name, which avoids interpolating a file path."""

TableQueryArg = Annotated[
    str | None,
    Field(
        description=(
            f"SQL SELECT over the table, which is always named '{_RELATION}', "
            f"e.g. \"SELECT region, SUM(amount) AS total FROM {_RELATION} "
            'GROUP BY region ORDER BY total DESC". Supports the usual '
            "aggregates, WHERE, HAVING, ORDER BY, CTEs, window functions, and "
            "self-joins. Omit it to get the columns, their types, the row "
            "count, and a few sample rows, which is the cheap first call."
        ),
    ),
]

TableSheetArg = Annotated[
    str | None,
    Field(
        description=(
            "Worksheet to query in a spreadsheet that has more than one. "
            "Defaults to the first sheet; omit the query to see them all."
        ),
    ),
]


@dataclass(slots=True, frozen=True)
class TableResult:
    """The rows a query returned, with the schema they came from."""

    file_path: str
    sheet: str | None = None
    sheets: tuple[str, ...] = ()
    """Every sheet in the workbook, so a follow-up can name a different one."""

    columns: tuple[str, ...] = ()
    dtypes: tuple[str, ...] = ()
    rows: tuple[tuple[str, ...], ...] = ()
    """Cells rendered as text, so a date or decimal survives the JSON trip."""

    total_rows: int | None = None
    """Rows in the source table, or ``None`` when a query decided the count."""

    truncated: bool = False
    source_encoding: str | None = None


@dataclass(slots=True, frozen=True)
class _Source:
    """A loaded table, with what the loading discovered about it."""

    frame: pl.LazyFrame
    sheet: str | None = None
    sheets: tuple[str, ...] = ()
    source_encoding: str | None = None


def _load_excel(path: Path, file_path: str, sheet: str | None) -> _Source:
    """Load one worksheet and retain the workbook's sheet names."""
    sheets = tuple(fastexcel.read_excel(path).sheet_names)

    if sheet is not None and sheet not in sheets:
        raise ToolRetry(
            f"'{file_path}' has no sheet named '{sheet}'. "
            f"Available sheets: {', '.join(sheets)}."
        )

    name = sheet if sheet is not None else sheets[0]

    # Excel has no lazy scan, but only the query result reaches the context.
    return _Source(
        frame=pl.read_excel(path, sheet_name=name).lazy(),
        sheet=name,
        sheets=sheets,
    )


def _load(path: Path, file_path: str, sheet: str | None, *, decode: bool) -> _Source:
    """Load *path* as a lazy frame, however its format has to be read."""
    suffix = path.suffix.lower()

    if suffix == ".parquet":
        return _Source(frame=pl.scan_parquet(path))

    if suffix not in _DELIMITED_SUFFIXES:
        return _load_excel(path, file_path, sheet)

    separator = "\t" if suffix == ".tsv" else ","

    if not decode:
        return _Source(frame=pl.scan_csv(path, separator=separator))

    decoded = read_text_or_retry(path, file_path)

    return _Source(
        frame=pl.read_csv(decoded.text.encode(), separator=separator).lazy(),
        source_encoding=decoded.source_encoding,
    )


def _cell(value: object) -> str:
    """Render a cell without applying the model-facing display cap."""
    if value is None:
        return ""

    return str(value).replace("|", r"\|").replace("\n", " ")


@dataclass(slots=True, frozen=True)
class QueryTableTool(AsyncPathTool[TableResult]):
    """Query a tabular document with SQL instead of reading it line by line.

    Row, column, cell, and rendered-output budgets bound the result, and every
    cut is named in the formatted output.
    """

    max_rows: int = 100
    preview_rows: int = 5
    max_columns: int = 40
    max_cell_chars: int = 200
    max_formatted_chars: int = 50_000

    @override
    async def __call__(
        self,
        file_path: DocumentFilePathArg,
        query: TableQueryArg = None,
        sheet: TableSheetArg = None,
    ) -> ToolOutput[TableResult]:
        """Query a spreadsheet or delimited document with SQL.

        Runs a SQL SELECT against the file, which is always addressed as ``t``,
        and returns the resulting rows.  Prefer this over reading a table
        document: filtering and aggregating in the query costs a fraction of
        the context that reading the rows would, and it cannot silently lose
        the trailing columns of a wide row the way a line read does.  Call it
        without a query first to learn the columns, their types, and the row
        count.
        """
        _sp, _local, absolute = resolve_file_or_retry(self.resolved_paths, file_path)

        if not is_tabular(file_path):
            raise ToolRetry(
                f"'{file_path}' is not a tabular file. Queryable formats: "
                f"{', '.join(sorted(TABULAR_SUFFIXES))}. Read anything else "
                f"with read_document.{sidecar_hint(file_path)}"
            )

        # Polars releases the GIL, but the call still blocks, so it stays off
        # the event loop.
        return await asyncio.to_thread(self._run, file_path, absolute, query, sheet)

    def _run(
        self, file_path: str, absolute: Path, query: str | None, sheet: str | None
    ) -> ToolOutput[TableResult]:
        """Attempt the scan, then retry once for a file that is not UTF-8."""
        retryable = absolute.suffix.lower() in _DELIMITED_SUFFIXES

        try:
            try:
                return self._attempt(file_path, absolute, query, sheet, decode=False)

            # A lazy scan discovers invalid UTF-8 only when it reaches it.
            # Other query failures use different PolarsError subclasses.
            except pl.exceptions.ComputeError:
                if not retryable:
                    raise

                return self._attempt(file_path, absolute, query, sheet, decode=True)

        except pl.exceptions.PolarsError as exc:
            detail = (
                f"query failed: {exc} The table is named '{_RELATION}'; call "
                "without a query to see its columns and their types."
                if query is not None
                else f"could not read the table: {exc}"
            )
            raise ToolRetry(detail) from exc

    def _attempt(
        self,
        file_path: str,
        absolute: Path,
        query: str | None,
        sheet: str | None,
        *,
        decode: bool,
    ) -> ToolOutput[TableResult]:
        """Load, query, and render one pass over the file."""
        source = _load(absolute, file_path, sheet, decode=decode)
        limit = self.preview_rows if query is None else self.max_rows
        frame = (
            source.frame
            if query is None
            else pl.SQLContext({_RELATION: source.frame}).execute(query)
        )

        # One row past the limit is what separates "all of it" from "the first
        # N", without collecting the rest to find out.
        collected = frame.head(limit + 1).collect()
        total = (
            int(source.frame.select(pl.len()).collect().item())
            if query is None
            else None
        )

        rows = collected.head(limit)
        result = TableResult(
            file_path=file_path,
            sheet=source.sheet,
            sheets=source.sheets,
            columns=tuple(rows.columns),
            dtypes=tuple(str(dtype) for dtype in rows.dtypes),
            rows=tuple(tuple(_cell(value) for value in row) for row in rows.iter_rows()),
            total_rows=total,
            truncated=collected.height > limit,
            source_encoding=source.source_encoding,
        )

        # Keep the structured rows aligned with the model-facing output.
        body, dropped = self._render(result, query)

        if dropped:
            result = replace(
                result, rows=result.rows[: len(result.rows) - dropped], truncated=True
            )

        return ToolOutput(
            data=result, formatted=body + hint_suffix(self._hints(result, query))
        )

    def _render(self, result: TableResult, query: str | None) -> tuple[str, int]:
        """Render the table and report how many row lines were omitted."""
        lines = self._preamble(result, query)

        if not result.rows:
            return "\n".join([*lines, "(no rows)"]), 0

        width = min(len(result.columns), self.max_columns)
        lines += [
            f"| {' | '.join(result.columns[:width])} |",
            f"| {' | '.join('---' for _ in range(width))} |",
        ]
        spent = sum(len(line) + 1 for line in lines)
        body, dropped = cap_lines(
            (
                f"| {' | '.join(truncate_line(cell, self.max_cell_chars) for cell in row[:width])} |"
                for row in result.rows
            ),
            self.max_formatted_chars - spent,
        )

        return "\n".join([*lines, body]), dropped

    def _preamble(self, result: TableResult, query: str | None) -> list[str]:
        """Render the summary line, plus the schema when it was asked for."""
        parts = [result.file_path]

        if result.sheet is not None:
            parts.append(f"sheet '{result.sheet}'")

        if result.total_rows is not None:
            parts.append(f"{result.total_rows} {pluralize(result.total_rows, 'row')}")

        if result.source_encoding is not None:
            parts.append(f"decoded from {result.source_encoding}")

        lines = [", ".join(parts)]

        if query is not None:
            return lines

        if len(result.sheets) > 1:
            lines += ["", f"sheets: {', '.join(result.sheets)}"]

        # One column per line rather than a wide preview: the schema is the
        # point of this call, and a vertical list of it is never too wide.
        lines += ["", "columns:"]
        lines += [
            f"{name}: {dtype}"
            for name, dtype in zip(result.columns, result.dtypes, strict=True)
        ]

        return [*lines, "", "sample:"]

    def _hints(self, result: TableResult, query: str | None) -> list[str]:
        """Name every cut, so a partial table cannot read as a complete one."""
        hints: list[str] = []

        if len(result.columns) > self.max_columns:
            hints.append(
                f"{self.max_columns} of {len(result.columns)} columns shown, "
                "name the ones you need in the SELECT"
            )

        if result.truncated and query is not None:
            hints.append(
                f"{len(result.rows)} rows shown, narrow with WHERE or "
                "aggregate with GROUP BY"
            )

        if query is None:
            hints.append(f"pass a SQL query over '{_RELATION}' to filter or aggregate")

        return hints
