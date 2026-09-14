from collections.abc import Callable, Sequence
from functools import lru_cache
from pathlib import Path

from dyn.commons.tabular_data import assemble_table, render_row
from dyn.serialization.simple.excel import (
    excel_to_sheets,
    sheet_to_headers_values,
)
from dyn.serialization.simple.json import (
    to_headers_rows as json_to_header_rows,
)
from dyn.util import get_token_count


def _pack_units(
    n_units: int,
    render: Callable[[int, int], str],
    split_oversized: Callable[[int], list[str]],
    limit: int,
    marginal_cost: Callable[[int], int] | None = None,
) -> list[str]:
    """LLM-GENERATED.
    Greedily pack units[start:start+count] into tables of at most `limit` tokens.

    render(start, count) -> the assembled table for that slice of units
    split_oversized(i)   -> tables for unit i, which alone exceeds the limit
    marginal_cost(j)     -> added token cost of appending unit j to an existing
                            table. When given, growth is checked incrementally
                            (cheap, approximate); otherwise each candidate table
                            is re-assembled and counted exactly.
    """
    i: int = 0
    tables: list[str] = []
    while i < n_units:
        length: int = get_token_count(render(i, 1))
        if length > limit:
            tables += split_oversized(i)
            i += 1
            continue

        count: int = 1
        while i + count < n_units:
            if marginal_cost is None:
                if get_token_count(render(i, count + 1)) > limit:
                    break
            else:
                added: int = marginal_cost(i + count)
                if length + added > limit:
                    break
                length += added
            count += 1

        tables.append(render(i, count))
        i += count
    return tables


def chunked_table(
    title: str,
    headers: Sequence[str],
    values: Sequence[Sequence[str]],
    limit: int = 2048,
) -> list[str]:
    """Splits a table into chunks. Tries to fit as many rows into a chunk. If a single row
    exceeds the limit, tries to fit as many cells. If a single cell exceeds the limit, the cell
    is subdivided using recursive character-based splitting.

    Args:
        title (str): The title of the table
        headers (Sequence[str]): The column headers
        values (Sequence[Sequence[str]]): The table values to process
        limit (int): The maximum number of items per chunk

    Returns:
        list[str]: A list of table strings
    """
    return _pack_units(
        len(values),
        lambda s, c: assemble_table(title, headers, values[s : s + c]),
        lambda i: _handle_long_row(title, headers, values[i], limit),
        limit,
        lambda i: get_token_count("\n" + render_row(values[i])),
    )


def _handle_long_row(
    title: str, headers: Sequence[str], values: Sequence[str], limit: int
) -> list[str]:
    return _pack_units(
        len(values),
        lambda s, c: assemble_table(title, headers[s : s + c], [values[s : s + c]]),
        lambda i: _handle_long_cell(title, headers[i], values[i], limit),
        limit,
        lambda i: get_token_count(
            f"**{headers[i]}**" + " --- " + str(values[i]) + " | " * 3
        ),
    )


@lru_cache(maxsize=16)
def _recursive_chunker(chunk_size: int):
    from chonkie import RecursiveChunker

    return RecursiveChunker(tokenizer="o200k_base", chunk_size=chunk_size)


def _handle_long_cell(title: str, header: str, value: str, limit: int) -> list[str]:
    from chonkie import Chunk

    fixed = f"# Table {title}\n\n| {header} |\n| --- |\n"
    fixed_cost = get_token_count(fixed)
    chunker = _recursive_chunker(limit - fixed_cost)
    return [
        assemble_table(title, [header], [[chunk.text]])
        for chunk in chunker(value)
        if isinstance(chunk, Chunk)
    ]


def excel_to_markdown_chunks(path: Path, limit: int) -> list[str]:
    """Convert all Excel sheets in the file into markdown table chunks.

    Args:
        path (Path): The path to the Excel file
        limit (int): The maximum number of rows per table chunk

    Returns:
        list[str]: A list of markdown table strings
    """
    tables: list[str] = []
    for sheet in excel_to_sheets(path):
        headers, rows = sheet_to_headers_values(sheet)
        tables += chunked_table(sheet.title, headers, rows.tolist(), limit)
    return tables


def json_to_markdown_chunks(path: Path, limit: int) -> list[str]:
    """Convert a Markdown table into table chunks.

    Args:
        path (Path): The file path to process.
        limit (int): The maximum number of rows per table chunk.

    Returns:
        list[str]: A list of markdown table strings
    """
    ret: list[str] = []
    for title, headers, rows in json_to_header_rows(path):
        ret += chunked_table(title, headers, rows, limit)
    return ret
