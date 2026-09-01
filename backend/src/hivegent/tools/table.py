"""Query tabular documents with SQL instead of reading projected rows."""

import asyncio
import re
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass, replace
from itertools import chain
from pathlib import Path
from typing import Annotated, ClassVar, override

import fastexcel
import polars as pl
from pydantic import Field

from ..converters import DELIMITED_SUFFIXES, DELIMITERS, TABULAR_SUFFIXES, is_tabular
from ..humanize import pluralize
from .base import (
    ToolOutput,
    ToolRetry,
    read_text_or_retry,
    resolve_file_or_retry,
    sidecar_hint,
)
from .formatting import hint_suffix, truncate_line
from .sink import OutputPathArg, RedirectedOutput, RedirectingPathTool

__all__ = [
    "QueriedTable",
    "QueryTableTool",
    "TableFilePathArg",
    "TableQueryArg",
    "TableResult",
    "TableRowLimitArg",
    "TableSheetArg",
    "TextColumn",
]

_RELATION = "t"
"""Fixed name of the first relation, which avoids interpolating a file path."""


def _relation(index: int) -> str:
    """Name the *index*-th table a query addresses: ``t``, ``t2``, ``t3``, ...

    Positional rather than derived from the filename, which in an exported
    workbook is a sentence with spaces in it and would need quoting in every
    query that named it.
    """
    return _RELATION if not index else f"{_RELATION}{index + 1}"


_LEADING_ZERO = r"^[+-]?0\d"
"""Zero-padded digits are an identifier (a zip code, an EAN), not a number."""

_DATETIME = r"^\d{4}-\d{1,2}-\d{1,2}[ T]\d"
"""The shape Polars infers a format for, which is what makes that parse total."""

_DEFAULT_ROW_LIMIT = 100
_MAX_ROW_LIMIT = 1000

_UNPARSED_SAMPLE = 3
"""Distinct unparsable values named per column, enough to say what they are."""

_BARE_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
"""What SQL takes unquoted; anything else has to be spelled in double quotes."""


def _quoted(name: str) -> str:
    """Spell a column the way a query has to type it.

    The schema listing is the only place a model ever sees a column name, so it
    has to be the spelling that parses: a header carrying a space, a unit, or a
    parenthesis is the norm in an exported spreadsheet and is a syntax error
    unquoted.

    >>> _quoted("amount")
    'amount'
    >>> _quoted("Zulaufmenge (D)")
    '"Zulaufmenge (D)"'
    """
    if _BARE_IDENTIFIER.fullmatch(name):
        return name

    escaped = name.replace('"', '""')

    return f'"{escaped}"'


def _query_mentions(query: str, name: str) -> bool:
    """Return whether SQL *query* spells column *name* as an identifier.

    >>> _query_mentions("SELECT id FROM t", "id")
    True
    >>> _query_mentions("SELECT paid FROM t", "id")
    False
    >>> _query_mentions('SELECT "Total cost" FROM t', "Total cost")
    True
    """
    if not _BARE_IDENTIFIER.fullmatch(name):
        return _quoted(name).casefold() in query.casefold()

    escaped = re.escape(name)
    pattern = rf'(?<![A-Za-z0-9_])(?:{escaped}|"{escaped}")(?![A-Za-z0-9_])'

    return re.search(pattern, query, flags=re.IGNORECASE) is not None


def _quoting_hint(columns: Sequence[str]) -> str:
    """Say how a column name that is not a bare identifier has to be typed.

    Spelled with one of the file's own columns, since a parse error has nothing
    else to correct itself from: the schema call it would otherwise be sent
    back to is the one the query came from.
    """
    example = next((name for name in columns if _quoted(name) != name), None)
    if example is None:
        return ""

    return (
        " A column name that is not a bare identifier must be double-quoted, "
        f"as in `SELECT {_quoted(example)} FROM {_RELATION}`."
    )


def _naming(values: Sequence[str]) -> str:
    """Spell the values a column could not parse, when any were sampled.

    The count says a column is mixed; the values say what it is mixed with,
    which is what the answer turns on.  Naming them is what lets that be
    decided before a program crashes on one, or worse, quietly coerces it.
    """
    if not values:
        return ""

    spelled = ", ".join(f'"{truncate_line(value, 40)}"' for value in values)

    return f"; unparsed: {spelled}"


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


def _boolean(text: pl.Expr) -> pl.Expr:
    """Parse the two boolean literals Polars infers from delimited files."""
    return text.str.to_lowercase().replace_strict(
        {"true": True, "false": False}, default=None, return_dtype=pl.Boolean
    )


_COERCIONS = (
    _numeric(pl.Int64()),
    # Wider than an Int64 and still exact, tried before the float that would
    # round it: a 20-digit identifier reaches a query as itself, not as 1e+20.
    _numeric(pl.Int128()),
    _numeric(pl.Float64()),
    # An explicit format rejects an impossible day that a shape test would
    # wave through, and still reads a single-digit month.
    _Coercion(pl.Date(), lambda text: text.str.to_date("%Y-%m-%d", strict=False)),
    _Coercion(
        pl.Datetime(), lambda text: text.str.to_datetime(strict=False), guard=_DATETIME
    ),
    _Coercion(pl.Boolean(), _boolean, guard=r"(?i)^(true|false)$"),
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
    unparsed: tuple[str, ...] = ()
    """A few distinct values that did not parse, as they appear in the file."""

    @property
    def complete(self) -> bool:
        """Whether every value parses, which is what makes retyping lossless."""
        return self.parsed == self.total


TableFilePathArg = Annotated[
    str | list[str],
    Field(
        description=(
            "Full workspace path of the table to query, or a list of paths to "
            f"query together. The first is addressed as '{_RELATION}', the "
            f"second '{_RELATION}2', the third '{_RELATION}3', so a join reads "
            f"\"FROM {_RELATION} JOIN {_RELATION}2 ON ...\"."
        ),
    ),
]

TableQueryArg = Annotated[
    str | None,
    Field(
        description=(
            f"Polars SQL SELECT over the tables, the first named '{_RELATION}', "
            f'e.g. "SELECT region, SUM(amount) AS total FROM {_RELATION} '
            'GROUP BY region ORDER BY total DESC". Supports the usual '
            "aggregates, WHERE, HAVING, ORDER BY, CTEs, window functions, "
            "joins, and `SHOW TABLES`. It is Polars' dialect and not a "
            "database's, so a function it refuses is named in the error and "
            "may exist under another spelling worth trying. Omit the query to "
            "get the columns, their types, the row count, and a few sample "
            "rows, which is the cheap first call. Numbers and dates are "
            "already typed as such, so cast only a column still shown as "
            "String."
        ),
    ),
]

TableSheetArg = Annotated[
    str | None,
    Field(
        description=(
            "Worksheet to query in a spreadsheet that has more than one, "
            "applied to every spreadsheet given. Defaults to the first sheet; "
            "omit the query to see them all."
        ),
    ),
]

TableRowLimitArg = Annotated[
    int,
    Field(
        description=(
            f"Maximum query rows to return. Defaults to {_DEFAULT_ROW_LIMIT}, "
            "increase it when the task needs more complete row-level data."
        ),
        ge=1,
        le=_MAX_ROW_LIMIT,
    ),
]


@dataclass(slots=True, frozen=True)
class QueriedTable:
    """One file the query was run against, under the SQL name it took."""

    name: str
    """What the query addresses it as: ``t``, then ``t2``, ``t3``, ..."""

    file_path: str
    sheet: str | None = None
    sheets: tuple[str, ...] = ()
    """Every sheet in the workbook, so a follow-up can name a different one."""

    source_encoding: str | None = None
    text_columns: tuple[TextColumn, ...] = ()
    """Text columns that hold numbers or dates, retyped where every value did."""


@dataclass(slots=True, frozen=True)
class TableResult:
    """The rows a query returned, with the tables they came from.

    The two halves are kept apart because they answer different questions and
    no longer stand one to one: ``tables`` describes each file that was
    registered, while the columns and rows describe what the query made of
    them, which for a join belongs to no single file.
    """

    tables: tuple[QueriedTable, ...] = ()
    columns: tuple[str, ...] = ()
    dtypes: tuple[str, ...] = ()
    rows: tuple[tuple[str, ...], ...] = ()
    """Cells rendered as text, so a date or decimal survives the JSON trip."""

    total_rows: int | None = None
    """Rows in the source table, or ``None`` when a query decided the count."""

    truncated: bool = False

    @property
    def text_columns(self) -> tuple[TextColumn, ...]:
        """Every registered table's text columns, under their own names.

        Unqualified, because a column belongs to the table that holds it and
        :attr:`tables` already says which that is.  Where the two have to be
        spelled as one — a hint a join would have to type back — they are
        joined at the point that holds both.
        """
        return tuple(
            column for table in self.tables for column in table.text_columns
        )


@dataclass(slots=True, frozen=True)
class _Source:
    """A loaded table, with what the loading discovered about it."""

    frame: pl.LazyFrame
    file_path: str = ""
    sheet: str | None = None
    sheets: tuple[str, ...] = ()
    source_encoding: str | None = None
    text_columns: tuple[TextColumn, ...] = ()
    strings: tuple[str, ...] = ()
    """Columns still text after coercion, the ones a query has to cast."""

    report_complete: bool = True
    """Whether losslessly converted text columns should be reported."""

    @property
    def columns(self) -> tuple[str, ...]:
        """Every column name, as the loaded frame declares them."""
        return tuple(self.frame.collect_schema().names())

    def queried(self, name: str) -> QueriedTable:
        """Describe this table as the result reports it, under its SQL name."""
        return QueriedTable(
            name=name,
            file_path=self.file_path,
            sheet=self.sheet,
            sheets=self.sheets,
            source_encoding=self.source_encoding,
            text_columns=self.text_columns,
        )


def _column_counts(name: str) -> Iterator[pl.Expr]:
    """The value counts one text column is judged by, in a fixed order."""
    text = _text(name)
    filled = text.is_not_null() & text.ne("")
    yield filled.sum()
    yield (filled & text.str.contains(_LEADING_ZERO)).sum()

    for coercion in _COERCIONS:
        yield (filled & coercion.matches(text)).sum()


def _with_unparsed(
    frame: pl.LazyFrame, typed: Sequence[tuple[TextColumn, _Coercion]]
) -> tuple[tuple[TextColumn, _Coercion], ...]:
    """Attach a sample of the values each mixed column could not parse.

    One collect for all of them rather than one apiece, and only for the
    columns that fell short: a column every value parsed in has nothing to
    sample.  Sorted so the same file always names the same values.
    """
    mixed = [pair for pair in typed if not pair[0].complete]

    if not mixed:
        return tuple(typed)

    # One column per frame, aliased so the filter and the read address it by a
    # name no source column can shadow.
    sample = "value"
    value = pl.col(sample)
    sampled = pl.collect_all(
        [
            frame.select(_text(column.name).alias(sample))
            .filter(value.is_not_null() & value.ne("") & ~coercion.matches(value))
            .unique()
            .sort(sample)
            .head(_UNPARSED_SAMPLE)
            for column, coercion in mixed
        ]
    )
    values = {
        column.name: tuple(collected[sample].to_list())
        for (column, _), collected in zip(mixed, sampled, strict=True)
    }

    return tuple(
        (replace(column, unparsed=values.get(column.name, ())), coercion)
        for column, coercion in typed
    )


def _coerce(source: _Source) -> _Source:
    """Retype every text column whose values all parse as one other dtype.

    A CSV column of numbers that one ``N/A`` keeps as text, or a spreadsheet
    that hands out its dates as strings, is what otherwise turns a plain
    ``SUM`` or ``WHERE`` into a dtype error and a ``TRY_CAST`` retry.  The
    conversion is applied only when it loses nothing: every non-empty value
    parses, so no cell silently becomes null, and a zero-padded identifier is
    never mistaken for a number.  An empty cell reads as a missing value,
    which is what it means in every format here.

    A column that falls short is recorded with the count that did parse and a
    sample of what did not, so a genuinely mixed one is reported before a query
    fails over it, not after.
    """
    frame = source.frame
    names = [
        name for name, dtype in frame.collect_schema().items() if dtype == pl.String
    ]

    if not names:
        return source

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
    stride = 2 + len(_COERCIONS)
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

        # Nothing parsing is a column of text, which is not a typing problem
        # to report: a name, a note, and a `1,5` decimal comma are one thing
        # to every test here, and flagging all three to catch the last would
        # put a hint on every string column of every table.
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

    sampled = _with_unparsed(frame, typed)
    converted = {column.name for column, _ in sampled if column.complete}
    text_columns = tuple(
        column
        for column, _ in sampled
        if source.report_complete or not column.complete
    )

    return replace(
        source,
        frame=frame.with_columns(
            coercion.convert(_text(column.name)).alias(column.name)
            for column, coercion in sampled
            if column.complete
        ),
        text_columns=text_columns,
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
        file_path=file_path,
        sheet=name,
        sheets=sheets,
    )


def _load(path: Path, file_path: str, sheet: str | None, *, decode: bool) -> _Source:
    """Load *path* as a lazy frame, however its format has to be read."""
    suffix = path.suffix.lower()

    if suffix == ".parquet":
        return _Source(frame=pl.scan_parquet(path), file_path=file_path)

    if suffix not in DELIMITED_SUFFIXES:
        return _load_excel(path, file_path, sheet)

    if not decode:
        return _Source(
            frame=pl.scan_csv(
                path,
                separator=DELIMITERS[suffix],
                infer_schema=False,
            ),
            file_path=file_path,
            report_complete=False,
        )

    decoded = read_text_or_retry(path, file_path)

    return _Source(
        frame=pl.read_csv(
            decoded.text.encode(),
            separator=DELIMITERS[suffix],
            infer_schema=False,
        ).lazy(),
        file_path=file_path,
        source_encoding=decoded.source_encoding,
        report_complete=False,
    )


def _cell(value: object) -> str:
    """Render a cell without applying the model-facing display cap."""
    if value is None:
        return ""

    return str(value).replace("|", r"\|").replace("\n", " ")


@dataclass(slots=True, frozen=True)
class QueryTableTool(RedirectingPathTool[TableResult]):
    """Query a tabular document with SQL instead of reading it line by line.

    Row, column, cell, and rendered-output budgets bound the result, and every
    cut is named in the formatted output.
    """

    injectable: ClassVar[bool] = True
    """Monty decodes no spreadsheet, so a program can only be handed this."""

    max_rows: int = _MAX_ROW_LIMIT
    preview_rows: int = 5
    max_columns: int = 40
    max_cell_chars: int = 200
    max_formatted_chars: int = 50_000
    max_named_columns: int = 10
    """Cap on the columns a hint spells out before it is noise itself."""

    @override
    async def __call__(
        self,
        file_path: TableFilePathArg,
        query: TableQueryArg = None,
        sheet: TableSheetArg = None,
        row_limit: TableRowLimitArg = _DEFAULT_ROW_LIMIT,
        output_path: OutputPathArg = None,
    ) -> ToolOutput[TableResult | RedirectedOutput]:
        """Query one or more spreadsheets or delimited documents with SQL.

        Runs a Polars SQL SELECT against the files and returns the resulting
        rows.  One file is addressed as ``t``; give a list to query several
        together, where the first is ``t``, the second ``t2``, and so on, so a
        join reads ``FROM t JOIN t2 ON ...``.  Prefer this over reading a table
        document: filtering and aggregating in the query costs a fraction of
        the context that reading the rows would, and it cannot silently lose
        the trailing columns of a wide row the way a line read does.  Call it
        without a query first to learn the columns, their types, and the row
        count.

        The dialect is Polars SQL, not a database's: it covers the usual
        aggregates, WHERE, GROUP BY, HAVING, ORDER BY, CTEs, window functions,
        and joins, but a function it lacks is refused by name and often exists
        under another, so read the refusal and try Polars' spelling rather than
        abandoning the query.  ``SHOW TABLES`` lists what is registered.
        """
        paths = [file_path] if isinstance(file_path, str) else list(file_path)

        if not paths:
            raise ToolRetry("Name at least one table to query.")

        resolved: list[tuple[str, Path]] = []

        for path in paths:
            _sp, _local, absolute = resolve_file_or_retry(self.resolved_paths, path)

            if not is_tabular(path):
                raise ToolRetry(
                    f"'{path}' is not a tabular file. Queryable formats: "
                    f"{', '.join(sorted(TABULAR_SUFFIXES))}. Read anything else "
                    f"with read_document.{sidecar_hint(path)}"
                )

            resolved.append((path, absolute))

        # Polars releases the GIL, but the call still blocks, so it stays off
        # the event loop.
        result = await asyncio.to_thread(
            self._run, tuple(resolved), query, sheet, row_limit
        )

        return await self.redirect(result, output_path)

    def _run(
        self,
        resolved: tuple[tuple[str, Path], ...],
        query: str | None,
        sheet: str | None,
        row_limit: int,
    ) -> ToolOutput[TableResult]:
        """Load every file, then run the query against what they turned out to be."""
        sources = tuple(
            self._open(file_path, absolute, sheet) for file_path, absolute in resolved
        )

        try:
            return self._query(sources, query, row_limit)

        except pl.exceptions.PolarsError as exc:
            raise ToolRetry(self._failure(exc, sources, query)) from exc

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
                if absolute.suffix.lower() not in DELIMITED_SUFFIXES:
                    raise

                return _coerce(_load(absolute, file_path, sheet, decode=True))

        except pl.exceptions.PolarsError as exc:
            raise ToolRetry(f"could not read the table: {exc}") from exc

    def _failure(
        self,
        exc: pl.exceptions.PolarsError,
        sources: tuple[_Source, ...],
        query: str | None,
    ) -> str:
        """Explain a failed query, naming the columns a cast would rescue."""
        detail = (
            f"query failed: {exc} The tables are named "
            f"{', '.join(_relation(index) for index in range(len(sources)))}; "
            "call without a query to see their columns and types, or run "
            "`SHOW TABLES`."
        )

        # A parse error is almost always an unquoted header, since an exported
        # sheet names its columns with spaces, units, and parentheses; the
        # schema listing already spells them quoted, so name one of its own.
        # A refused *function* is the dialect instead, and worth saying so:
        # Polars SQL is not a database's, and a run that reads "unsupported
        # function" as "SQL cannot do this" leaves for Python over a spelling.
        if isinstance(
            exc, pl.exceptions.SQLInterfaceError | pl.exceptions.SQLSyntaxError
        ):
            dialect = (
                " This is the Polars SQL dialect rather than a database's, so a "
                "function it refuses may exist under another name — try Polars' "
                "own spelling before giving up on the query."
                if "unsupported function" in str(exc).lower()
                else ""
            )

            return (
                detail
                + dialect
                + _quoting_hint([name for s in sources for name in s.columns])
            )

        # A dtype error over a column the coercion pass had to leave as text
        # is that text, so name it rather than leaving the cast to guesswork.
        named = [
            _quoted(name)
            for source in sources
            for name in source.strings
            if query and _query_mentions(query, name)
        ]

        if named and isinstance(
            exc, pl.exceptions.InvalidOperationError | pl.exceptions.ComputeError
        ):
            detail += (
                " "
                + self._named(
                    "These columns hold text that is not a number or a date",
                    named,
                )
                + ". Wrap one in TRY_CAST(col AS DOUBLE) to compare or "
                "aggregate it, which reads a value that does not parse as NULL."
            )

        return detail

    def _query(
        self,
        sources: tuple[_Source, ...],
        query: str | None,
        row_limit: int,
    ) -> ToolOutput[TableResult]:
        """Run the query over the loaded frames and render what it returned.

        Every file is registered whether or not the query names it, so a join
        needs nothing but the SQL, and ``SHOW TABLES`` answers from the same
        context the query runs in.  Without a query the first table is the
        subject, since a schema call has no join to describe.
        """
        limit = self.preview_rows if query is None else min(row_limit, self.max_rows)
        context = pl.SQLContext(
            {_relation(index): source.frame for index, source in enumerate(sources)}
        )
        frame = sources[0].frame if query is None else context.execute(query)

        # One row past the limit is what separates "all of it" from "the first
        # N", without collecting the rest to find out.
        collected = frame.head(limit + 1).collect()
        total = (
            int(sources[0].frame.select(pl.len()).collect().item())
            if query is None
            else None
        )

        frame_rows = collected.head(limit)
        columns = tuple(frame_rows.columns)
        dtypes = tuple(str(dtype) for dtype in frame_rows.dtypes)
        preamble = self._preamble(
            sources=sources,
            columns=columns,
            dtypes=dtypes,
            total_rows=total,
            query=query,
        )
        rows, body, display_cut = self._render(frame_rows, columns, preamble)

        # The two cuts are different facts and no longer share a flag: `rows`
        # holds everything the row limit allowed, so `truncated` says only that
        # the limit bound, while what the display dropped rides the hint.
        result = TableResult(
            tables=tuple(
                source.queried(_relation(index))
                for index, source in enumerate(sources)
            ),
            columns=columns,
            dtypes=dtypes,
            rows=rows,
            total_rows=total,
            truncated=collected.height > limit,
        )

        return ToolOutput(
            data=result,
            formatted=body
            + hint_suffix(
                self._hints(
                    result,
                    query,
                    display_cut=display_cut,
                    can_increase_rows=result.truncated and limit < self.max_rows,
                )
            ),
        )

    def _render(
        self,
        frame: pl.DataFrame,
        columns: tuple[str, ...],
        lines: list[str],
    ) -> tuple[tuple[tuple[str, ...], ...], str, bool]:
        """Render rows under the display budget, keeping every one of them.

        The budget binds what the model is shown and nothing else.  It used to
        end the loop, so the rows past it never reached ``TableResult.rows``
        either — and a redirect, which writes that structured result to a file
        and reports only its size, then wrote a truncated table while promising
        the whole of it.  A run that redirected 1000 rows to `.json` and
        computed from the file was working from 262 of them and could not tell.

        What the display drops is a rendering fact, reported as a hint; how
        many rows there are is a data fact, and both channels now agree on it.
        """
        if frame.is_empty():
            return (), "\n".join([*lines, "(no rows)"]), False

        width = min(len(columns), self.max_columns)
        lines += [
            f"| {' | '.join(columns[:width])} |",
            f"| {' | '.join('---' for _ in range(width))} |",
        ]
        spent = sum(len(line) + 1 for line in lines)
        kept: list[tuple[str, ...]] = []
        rendered: list[str] = []
        display_full = True

        for values in frame.iter_rows():
            row = tuple(_cell(value) for value in values)
            kept.append(row)

            if not display_full:
                continue

            line = f"| {' | '.join(truncate_line(cell, self.max_cell_chars) for cell in row[:width])} |"
            extra = len(line) + (1 if rendered else 0)

            if rendered and spent + extra > self.max_formatted_chars:
                display_full = False
                continue

            rendered.append(line)
            spent += extra

        return tuple(kept), "\n".join([*lines, *rendered]), not display_full

    def _preamble(
        self,
        sources: tuple[_Source, ...],
        columns: tuple[str, ...],
        dtypes: tuple[str, ...],
        total_rows: int | None,
        query: str | None,
    ) -> list[str]:
        """Render a summary line per table, plus the schema when asked for.

        Every table is named on its own line, since a query that can join them
        has to know what each one is called and a list of paths says nothing
        about which took which name.
        """
        lines = [
            self._summary(source, _relation(index), total_rows if not index else None)
            for index, source in enumerate(sources)
        ]

        if query is not None:
            return lines

        source = sources[0]

        if len(source.sheets) > 1:
            lines += ["", f"sheets: {', '.join(source.sheets)}"]

        # One column per line rather than a wide preview: the schema is the
        # point of this call, and a vertical list of it is never too wide.
        lines += ["", "columns:"]
        lines += [
            f"{_quoted(name)}: {dtype}"
            for name, dtype in zip(columns, dtypes, strict=True)
        ]

        return [*lines, "", "sample:"]

    def _summary(self, source: _Source, name: str, total_rows: int | None) -> str:
        """Say what one registered table is, and what a query addresses it as."""
        parts = [f"{name}: {source.file_path}"]

        if source.sheet is not None:
            parts.append(f"sheet '{source.sheet}'")

        if total_rows is not None:
            parts.append(f"{total_rows} {pluralize(total_rows, 'row')}")

        if source.source_encoding is not None:
            parts.append(f"decoded from {source.source_encoding}")

        return ", ".join(parts)

    def _hints(
        self,
        result: TableResult,
        query: str | None,
        *,
        display_cut: bool,
        can_increase_rows: bool,
    ) -> list[str]:
        """Name every cut, so a partial table cannot read as a complete one."""
        hints: list[str] = []

        if len(result.columns) > self.max_columns:
            hints.append(
                f"{self.max_columns} of {len(result.columns)} columns shown, "
                "name the ones you need in the SELECT"
            )

        if result.truncated and query is not None:
            increase = (
                f", increase row_limit up to {self.max_rows}"
                if can_increase_rows
                else ""
            )
            hints.append(
                f"{len(result.rows)} rows returned{increase}, narrow with WHERE, "
                "or aggregate with GROUP BY"
            )

        # Said apart from the row cut above, because only the rendering was
        # bound: every row is in the result, and an `output_path` writes all
        # of them however few of them are printed here.
        if display_cut:
            hints.append(
                f"all {len(result.rows)} rows are in the result; the table above "
                "stops at the display budget, so redirect with output_path or "
                "aggregate if you need the rest of them read"
            )

        # What is wrong with the data rides every call, not only the schema
        # call: the query that most needs to hear it is the one that named the
        # column, and a run that opens with a SELECT never asks for the schema
        # afterwards.
        hints += self._typing_hints(result, query)

        if query is None:
            hints.append(f"pass a SQL query over '{_RELATION}' to filter or aggregate")

        return hints

    def _typing_hints(self, result: TableResult, query: str | None) -> list[str]:
        """Say which text columns were retyped, and which a cast has to reach.

        A mixed column is the one worth spending a hint on, named with the
        share that parses and the values that did not.  The values are what
        make this one hint enough for every shape of table: a repeated label
        says the header spilled a row into the data, a `<100` says a reading
        past a measurement limit, a `0,05` says a decimal comma, and each
        wants a different answer that is not this tool's to pick.  Reporting
        the values and asking generalises where a rule per shape does not —
        a two-row header and a three-row header read the same here.

        The worst shortfall leads, so the cap can only ever cut the columns
        least worth reading about.  Scoped to the columns this call touched,
        since a warning about a column the query never named is noise read
        past, and it was the cap on that noise that once cut the only column
        a query was about from behind nine it was not.  A column counts as
        touched when the result carries it or the query spells it, and it
        takes both: ``SELECT *`` names no column at all, while a column
        filtered on but not selected reaches the result under no name.
        """
        qualify = len(result.tables) > 1
        columns = [
            # Spelled as a query would have to type it: bare against one table,
            # and `t2."Menge (D)"` against several, which is the qualified name
            # SQL takes and not the quoted `"t2.Menge (D)"` that spelling the
            # two halves as one identifier would produce.
            (f"{table.name}.{_quoted(column.name)}" if qualify else _quoted(column.name), column)
            for table in result.tables
            for column in table.text_columns
            if query is None
            or column.name in result.columns
            or _query_mentions(query, column.name)
        ]
        retyped = [name for name, column in columns if column.complete]
        mixed = sorted(
            ((name, column) for name, column in columns if not column.complete),
            key=lambda pair: pair[1].total - pair[1].parsed,
            reverse=True,
        )
        spelled = [
            f"{name} ({column.parsed} of {column.total} parse "
            f"as {column.dtype}{_naming(column.unparsed)})"
            for name, column in mixed
        ]
        hints: list[str] = []

        if retyped:
            hints.append(
                self._named("stored as text, queryable as the type shown", retyped)
            )

        if spelled:
            hints.append(
                self._named("mixed text, wrap in TRY_CAST to compare", spelled)
                + ". The values named are not numbers, so ask the user how each "
                "should be treated rather than substituting or skipping one, "
                "which changes every total drawn from that column"
            )

        return hints

    def _named(self, label: str, values: Sequence[str]) -> str:
        """Spell a capped list without letting the cap pass for the whole of it."""
        rest = len(values) - self.max_named_columns
        spelled = ", ".join(values[: self.max_named_columns])

        if rest <= 0:
            return f"{label}: {spelled}"

        return f"{label}: {spelled}, and {rest} more {pluralize(rest, 'column')}"
