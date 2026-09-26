from collections.abc import Sequence
from pathlib import Path

import polars as pl

from hivegent.dyn.commons.tabular_data import assemble_table


def _unnest(data: list[pl.Series], prefix: str = "") -> list[pl.Series]:
    """Recursively unnests a dataframe by converting structs with multiple fields into individual
    columns per field."""
    ret: list[pl.Series] = []
    for col in data:
        name = prefix + "." + col.name if prefix else col.name
        if isinstance(col.dtype, pl.Struct):
            ret += _unnest(list(col.struct.unnest().iter_columns()), name)
        else:
            ret.append(col.rename(name))
    return ret


def _incsert(d: dict[str, int], v: str) -> int:
    """Upsert with incrementing values. For renaming fields upon conflict."""
    if v in d:
        d[v] += 1
    else:
        d[v] = 0
    return d[v]


def _explode(data: list[pl.Series], idx: int) -> list[pl.Series]:
    """If a column holds lists of items, explodes the list into one item per row. The other rows are extended
    by repeating items."""
    series = data[idx]
    lengths = series.list.len()
    new_series: list[pl.Series] = []
    for i, s in enumerate(data):
        if i == idx:
            new_series.append(s.explode())
        else:
            # all other series have to be extended to match the exploded series' length
            # repeat_by([l1,l2,l3]) converts ["a", "b", "c"] into [["a"*l1], ["b"*l2], ["c"*l3]]
            extended_s = s.repeat_by(lengths)
            extended_s = extended_s.explode()
            new_series.append(extended_s)
    return new_series


def _explode_all(data: list[pl.Series]) -> list[pl.Series]:
    """Explode all list columns within the input data.

    Args:
        data (list[pl.Series]): The list of Polars Series to process

    Returns:
        list[pl.Series]: The exploded list of Series
    """
    for i, col in enumerate(data):
        if col.dtype.base_type() == pl.List:
            data = _explode(data, i)
    return data


def _explode_unnest(data: list[pl.Series]) -> list[pl.Series]:
    """Flatten nested JSON columns into a flat, rectangular set of Series.

    Alternates `_unnest` and `_explode_all` until a pass no longer
    changes the shape of the data, resulting in a completely flat table.

    Args:
        data (list[pl.Series]): The columns to flatten, possibly containing
            struct and list dtypes

    Returns:
        list[pl.Series]: Columns of scalar dtype and equal length, with unique
            names
    """
    lengths_start = [len(d) for d in data]
    data = _unnest(data)
    data = _explode_all(data)
    lengths = [len(d) for d in data]
    if len(lengths_start) != len(lengths):
        return _explode_unnest(data)
    for l1, l2 in zip(lengths_start, lengths):
        if l1 != l2:
            return _explode_unnest(data)
    next_keys: dict[str, int] = {}

    def rename_if_conflict(series: pl.Series) -> pl.Series:
        next_key = _incsert(next_keys, series.name)
        return (
            series.rename(series.name + "_" + str(next_key)) if next_key > 0 else series
        )

    return [rename_if_conflict(series) for series in data]


type TableList = Sequence[tuple[str, Sequence[str], Sequence[Sequence[str]]]]


def to_headers_rows(path: Path) -> TableList:
    """Converts a json file into a Sequence of headers and a Sequence of value rows.

    Args:
        path (Path): The file path

    Returns:
        TableList: A list of table structures
    """
    df = pl.read_json(path) if path.suffix == ".json" else pl.read_ndjson(path)
    if df.shape[0] != 1:
        df = pl.DataFrame(_explode_unnest(list(df.iter_columns())))
        df = df.cast(pl.String).fill_null("")
        rows = list(df.iter_rows())
        return [("", df.columns, rows)]
    # treat every column as its own table
    ret: TableList = []
    for col in df.iter_columns():
        df = pl.DataFrame(_explode_unnest([col]))
        df = df.cast(pl.String).fill_null("")
        rows = list(df.iter_rows())
        ret.append((col.name, df.columns, rows))
    return ret


def to_markdown(path: Path) -> str:
    """Convert the file path into a Markdown table string.

    Args:
        path (Path): The file path

    Returns:
        str: The generated Markdown table string
    """
    tables = [
        assemble_table(title, headers, rows)
        for title, headers, rows in to_headers_rows(path)
    ]
    return "\n\n".join(tables)
