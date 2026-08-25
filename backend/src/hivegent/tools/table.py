"""Query tabular documents with SQL instead of reading projected rows."""

import asyncio
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass, replace
from functools import partial
from itertools import chain
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
    "TextColumn",
]

_DELIMITED_SUFFIXES = frozenset({".csv", ".tsv"})
"""Formats that can be retried through the shared text decoder."""

_RELATION = "t"
"""Fixed query relation name, which avoids interpolating a file path."""

_LEADING_ZERO = r"^[+-]?0\d"
"""Zero-padded digits are an identifier (a zip code, an EAN), not a number."""

_DATETIME = r"^\d{4}-\d{1,2}-\d{1,2}[ T]\d"
"""The shape Polars infers a format for, which is what makes that parse total."""


@dataclass(slots=True, frozen=True)
class _Coercion:
    """A dtype a text column takes on when every one of its values is one."""

    dtype: pl.DataType
    convert: Callable[[pl.Expr], pl.Expr]
    guard: str | None = None
    """Shape to test instead, for a parse that raises rather than nulls."""

    numeric: bool = False
    """Whether one zero-padded value rules this dtype out for the column."""

    def matches(self, text: pl.Expr) -> pl.Expr:
        """Whether a value becomes this dtype, tested without ever raising."""
        if self.guard is not None:
            return text.str.contains(self.guard)

        return self.convert(text).is_not_null()


def _numeric(dtype: pl.DataType) -> _Coercion:
    """Build the coercion to *dtype*, a plain parse of every value."""
    return _Coercion(dtype, lambda text: text.cast(dtype, strict=False), numeric=True)


_COERCIONS = (
    _numeric(pl.Int64()),
    _numeric(pl.Float64()),
    # An explicit format rejects an impossible day that a shape test would
    # wave through, and still reads a single-digit month.
    _Coercion(pl.Date(), lambda text: text.str.to_date("%Y-%m-%d", strict=False)),
    _Coercion(
        pl.Datetime(), lambda text: text.str.to_datetime(strict=False), guard=_DATETIME
    ),
)
"""Tried in order, so the narrowest dtype that fits every value wins."""


def _text(name: str) -> pl.Expr:
    """Address a text column with its surrounding whitespace dropped."""
    return pl.col(name).str.strip_chars()


@dataclass(slots=True, frozen=True)
class TextColumn:
    """A column the file stores as text, and the dtype its values parse as."""

    name: str
    dtype: str
    parsed: int
    """Non-empty values that parse as *dtype*, of *total* that were tried."""

    total: int

    @property
    def complete(self) -> bool:
        """Whether every value parses, which is what makes retyping lossless."""
        return self.parsed == self.total


TableQueryArg = Annotated[
    str | None,
    Field(
        description=(
            f"SQL SELECT over the table, which is always named '{_RELATION}', "
            f'e.g. "SELECT region, SUM(amount) AS total FROM {_RELATION} '
            'GROUP BY region ORDER BY total DESC". Supports the usual '
            "aggregates, WHERE, HAVING, ORDER BY, CTEs, window functions, and "
            "self-joins. Omit it to get the columns, their types, the row "
            "count, and a few sample rows, which is the cheap first call. "
            "Numbers and dates are already typed as such, so cast only a "
            "column the schema still shows as String."
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

    text_columns: tuple[TextColumn, ...] = ()
    """Text columns that hold numbers or dates, retyped where every value did."""


@dataclass(slots=True, frozen=True)
class _Source:
    """A loaded table, with what the loading discovered about it."""

    frame: pl.LazyFrame
    sheet: str | None = None
    sheets: tuple[str, ...] = ()
    source_encoding: str | None = None
    text_columns: tuple[TextColumn, ...] = ()
    strings: tuple[str, ...] = ()
    """Columns still text after coercion, the ones a query has to cast."""


def _column_counts(name: str) -> Iterator[pl.Expr]:
    """The value counts one text column is judged by, in a fixed order."""
    text = _text(name)
    filled = text.is_not_null() & text.ne("")
    yield filled.sum()
    yield (filled & text.str.contains(_LEADING_ZERO)).sum()

    for coercion in _COERCIONS:
        yield (filled & coercion.matches(text)).sum()


def _coerce(source: _Source) -> _Source:
    """Retype every text column whose values all parse as one other dtype.

    A CSV column of numbers that one ``N/A`` keeps as text, or a spreadsheet
    that hands out its dates as strings, is what otherwise turns a plain
    ``SUM`` or ``WHERE`` into a dtype error and a ``TRY_CAST`` retry.  The
    conversion is applied only when it loses nothing: every non-empty value
    parses, so no cell silently becomes null, and a zero-padded identifier is
    never mistaken for a number.  An empty cell reads as a missing value,
    which is what it means in every format here.

    A column that falls short is recorded with the count that did parse, so a
    genuinely mixed one is reported before a query fails over it, not after.
    """
    frame = source.frame
    names = [
        name for name, dtype in frame.collect_schema().items() if dtype == pl.String
    ]

    if not names:
        return source

    stride = 2 + len(_COERCIONS)
    counts = (
        frame.select(
            expr.alias(str(position))
            for position, expr in enumerate(
                chain.from_iterable(_column_counts(name) for name in names)
            )
        )
        .collect()
        .row(0)
    )
    typed: list[tuple[TextColumn, _Coercion]] = []

    for index, name in enumerate(names):
        base = index * stride
        # One zero-padded value makes the whole column an identifier, so it is
        # neither retyped nor reported as a number a cast would recover.
        padded = counts[base + 1]
        hits = [
            0 if coercion.numeric and padded else counts[base + 2 + rank]
            for rank, coercion in enumerate(_COERCIONS)
        ]
        # ``max`` keeps the first of equal counts, so a whole number stays one.
        rank = max(range(len(hits)), key=hits.__getitem__)

        if not hits[rank]:
            continue

        coercion = _COERCIONS[rank]
        column = TextColumn(
            name=name,
            dtype=type(coercion.dtype).__name__,
            parsed=hits[rank],
            total=counts[base],
        )
        typed.append((column, coercion))

    converted = {column.name for column, _ in typed if column.complete}

    return replace(
        source,
        frame=frame.with_columns(
            coercion.convert(_text(column.name)).alias(column.name)
            for column, coercion in typed
            if column.complete
        ),
        text_columns=tuple(column for column, _ in typed),
        strings=tuple(name for name in names if name not in converted),
    )


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
    # Reading through Polars rather than off `book` narrows a column of whole
    # numbers back to an integer, which Excel stores as a double either way.
    return _Source(
        frame=pl.read_excel(path, sheet_name=name, infer_schema_length=None).lazy(),
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

    # Inferring over the whole file rather than the default 100-row window:
    # a column typed by its first rows reads a late "N/A" as a broken number
    # instead of as text, which fails the scan outright.  The result is pinned
    # onto the frame that gets queried, since leaving `infer_schema_length` at
    # None re-runs that whole-file pass on every later collect.
    if not decode:
        scan = partial(pl.scan_csv, path, separator=separator)
        schema = scan(infer_schema_length=None, try_parse_dates=True).collect_schema()

        return _Source(frame=scan(schema=schema))

    decoded = read_text_or_retry(path, file_path)

    return _Source(
        frame=pl.read_csv(
            decoded.text.encode(),
            separator=separator,
            infer_schema_length=None,
            try_parse_dates=True,
        ).lazy(),
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
    max_named_columns: int = 10
    """Cap on the columns a hint spells out before it is noise itself."""

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
        """Load the file, then run the query against what it turned out to be."""
        source = self._open(file_path, absolute, sheet)

        try:
            return self._query(file_path, source, query)

        except pl.exceptions.PolarsError as exc:
            raise ToolRetry(self._failure(exc, source, query)) from exc

    def _open(self, file_path: str, absolute: Path, sheet: str | None) -> _Source:
        """Load and retype the file, retrying once for one that is not UTF-8.

        Coercion is what forces the read: every text column is scanned here,
        so a file in a legacy encoding fails now rather than mid-query, and
        the query phase is left with only its own failures to report.
        """
        try:
            try:
                return _coerce(_load(absolute, file_path, sheet, decode=False))

            # Invalid UTF-8 is reached only once a column is read, and every
            # other read failure uses a different PolarsError subclass.
            except pl.exceptions.ComputeError:
                if absolute.suffix.lower() not in _DELIMITED_SUFFIXES:
                    raise

                return _coerce(_load(absolute, file_path, sheet, decode=True))

        except pl.exceptions.PolarsError as exc:
            raise ToolRetry(f"could not read the table: {exc}") from exc

    def _failure(
        self, exc: pl.exceptions.PolarsError, source: _Source, query: str | None
    ) -> str:
        """Explain a failed query, naming the columns a cast would rescue."""
        detail = (
            f"query failed: {exc} The table is named '{_RELATION}'; call "
            "without a query to see its columns and their types."
        )

        # A dtype error over a column the coercion pass had to leave as text
        # is that text, so name it rather than leaving the cast to guesswork.
        named = [name for name in source.strings if query and name in query]

        if named and isinstance(
            exc, pl.exceptions.InvalidOperationError | pl.exceptions.ComputeError
        ):
            detail += (
                " These columns hold text that is not a number or a date: "
                f"{', '.join(named[: self.max_named_columns])}. Wrap one in "
                "TRY_CAST(col AS DOUBLE) to compare or aggregate it, which "
                "reads a value that does not parse as NULL."
            )

        return detail

    def _query(
        self, file_path: str, source: _Source, query: str | None
    ) -> ToolOutput[TableResult]:
        """Run the query over the loaded frame and render what it returned."""
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
            rows=tuple(
                tuple(_cell(value) for value in row) for row in rows.iter_rows()
            ),
            total_rows=total,
            truncated=collected.height > limit,
            source_encoding=source.source_encoding,
            text_columns=source.text_columns,
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
            hints += self._typing_hints(result.text_columns)
            hints.append(f"pass a SQL query over '{_RELATION}' to filter or aggregate")

        return hints

    def _typing_hints(self, columns: Sequence[TextColumn]) -> list[str]:
        """Say which text columns were retyped, and which a cast has to reach.

        A mixed column is the one worth spending a hint on: naming it with
        the share that parses is what lets the first query wrap it in
        ``TRY_CAST``, instead of learning it from a failure.
        """
        retyped = [column.name for column in columns if column.complete]
        mixed = [
            f"{column.name} ({column.parsed} of {column.total} parse as {column.dtype})"
            for column in columns
            if not column.complete
        ]

        return [
            f"{label}: {', '.join(spelled[: self.max_named_columns])}"
            for label, spelled in (
                ("stored as text, queryable as the type shown", retyped),
                ("mixed text, wrap in TRY_CAST to compare", mixed),
            )
            if spelled
        ]
