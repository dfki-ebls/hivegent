import numbers
import re
from collections.abc import Iterable, Iterator, Sequence
from pathlib import Path
from typing import cast
from zipfile import ZipFile

import numpy as np
import openpyxl
from openpyxl.cell.read_only import EmptyCell, ReadOnlyCell
from openpyxl.worksheet._read_only import ReadOnlyWorksheet
from openpyxl.worksheet.cell_range import CellRange

from hivegent.dyn.commons.tabular_data import (
    BoolMatrix,
    ObjMatrix,
    StrMatrix,
    assemble_table,
    merge_cell,
)

_ReadOnlyRow = tuple[ReadOnlyCell | EmptyCell, ...]


def read_only_rows(sheet: ReadOnlyWorksheet) -> Iterator[_ReadOnlyRow]:
    """LLM-GENERATED. Fixes a wrong stub alias.

    Args:
        sheet (ReadOnlyWorksheet): The worksheet to read rows from

    Returns:
        Iterator[_ReadOnlyRow]: An iterator of read-only rows
    """
    return cast(Iterator[_ReadOnlyRow], sheet.iter_rows())


def find_symbol(fmt: str) -> str:
    """Inspects a cell and returns its semantic formatting.

    Args:
        fmt (str): The format string to analyze

    Returns:
        str: The found symbol or extracted substring
    """
    if "%" in fmt:
        return "%"
    if "[$" in fmt:
        return fmt[fmt.find("[$") + 2 : fmt.find("-")]
    return ""


def sheet_to_matrices(
    sheet: ReadOnlyWorksheet,
) -> tuple[ObjMatrix, BoolMatrix, StrMatrix]:
    """Converts an Excel sheet into three matrices for the values, bolds and formattings.

    Args:
        sheet (ReadOnlyWorksheet): The worksheet to process

    Returns:
        tuple[ObjMatrix, BoolMatrix, StrMatrix]: A tuple containing value, bold, and format matrices
    """
    shape = (sheet.max_row or 0, sheet.max_column or 0)
    vals = np.empty(shape, dtype=object)
    bolds = np.zeros(shape, dtype=bool)
    formats = np.zeros(shape, dtype=np.dtype("U4"))
    style_cache: dict[int, tuple[bool, str]] = {}

    for i, row in enumerate(read_only_rows(sheet)):
        r_vals: list[object] = []
        r_bolds: list[bool] = []
        r_formats: list[str] = []
        for cell in row:
            if isinstance(cell, EmptyCell):
                r_vals.append(None)
                r_bolds.append(False)
                r_formats.append("")
                continue

            r_vals.append(cell.value)

            key = cell._style_id
            hit = style_cache.get(key)
            if hit is None:
                hit = style_cache[key] = (
                    bool(cell.font.b),
                    find_symbol(cell.number_format),
                )
            bold, symbol = hit
            r_bolds.append(bold)
            r_formats.append(symbol)

        vals[i] = r_vals
        bolds[i] = r_bolds
        formats[i] = r_formats
    return vals, bolds, formats


def _worksheet_source(ws: ReadOnlyWorksheet) -> tuple[ZipFile, str]:
    """Return the archive file and worksheet path. fix missing private types. LLM-GENERATED.


    Args:
        ws (ReadOnlyWorksheet): The worksheet object

    Returns:
        tuple[ZipFile, str]: The archive file and worksheet path
    """
    return cast(ZipFile, getattr(ws.parent, "_archive")), cast(
        str, getattr(ws, "_worksheet_path")
    )


_MERGE_RE = re.compile(rb'<(?:\w+:)?mergeCell[^>]*\sref="([^"]+)"')


def read_only_merged_ranges(ws: ReadOnlyWorksheet) -> list[CellRange]:
    """LLM-GENERATED. Merged CellRanges for a ReadOnlyWorksheet, which has no .merged_cells."""
    archive, path = _worksheet_source(ws)
    data = archive.read(path)
    return [CellRange(m.group(1).decode()) for m in _MERGE_RE.finditer(data)]


def _merge_cells_excel(sheet: ReadOnlyWorksheet, vals: ObjMatrix):
    """Merge cells in the value matrix based on the worksheet's merge ranges.
    Args:
        sheet (ReadOnlyWorksheet): The worksheet to process
        vals (ObjMatrix): The values to merge

    Returns:
        None
    """
    for r in read_only_merged_ranges(sheet):
        # excel is 1-indexed
        # bounds are low col, low row, high col, high row
        rb = r.bounds
        bounds = (rb[0] - 1, rb[1] - 1, rb[2] - 1, rb[3] - 1)
        merge_cell(bounds, vals)


def apply_format(vals: ObjMatrix, formats: StrMatrix):
    """Apply specific string formatting rules to the entries of the value matrix based on corresponding format matrix.

    Args:
        vals (ObjMatrix): The matrix of values to be formatted
        formats (StrMatrix): The matrix of format strings

    Returns:
        None
    """
    fmt_rows: list[list[str]] = formats.tolist()
    val_rows = cast(list[list[object]], vals.tolist())
    for i, (row_vals, row_formats) in enumerate(zip(val_rows, fmt_rows)):
        r_val: list[object] = []
        for val, fmt in zip(row_vals, row_formats):
            if isinstance(val, numbers.Complex):
                if fmt == "%":
                    val *= 100
                if fmt:
                    val = str(val) + "\\" + fmt
            r_val.append(val)
        vals[i, :] = r_val


def filter_empty_fill_none(
    vals: ObjMatrix, bolds: BoolMatrix
) -> tuple[ObjMatrix, BoolMatrix]:
    """Drop all-empty rows/columns and replace remaining ``None`` cells with "".

    Returns the filtered matrices; ``bolds`` is filtered alongside ``vals`` so
    both keep the same shape.
    """
    mask = cast(BoolMatrix, vals == None)
    keep_rows = ~mask.all(axis=1)
    keep_cols = ~mask.all(axis=0)

    idx = np.ix_(keep_rows, keep_cols)
    vals = vals[idx]
    bolds = bolds[idx]

    vals[vals == None] = ""
    # dates, numbers without a format, ... are still native objects here
    stringify = np.frompyfunc(lambda v: v if isinstance(v, str) else str(v), 1, 1)
    if vals.size:
        vals[:, :] = stringify(vals)
    return vals, bolds


def _header_end(c_bolds: BoolMatrix) -> int:
    """LLM-GENERATED. row index of the first non-bold value in a column"""
    if not c_bolds.any():
        return -1
    start = int(c_bolds.argmax())
    rest = c_bolds[start:]
    if rest.all():
        return len(c_bolds)
    return start + int(rest.argmin())


def _no_classic_table(vals: StrMatrix, bolds: BoolMatrix) -> bool:
    """Whether bold cells cannot be read as a header band on top of the values.

    Both checks below mean the per-column split in :func:`split_header_values`
    would shift columns against each other, so the sheet is better emitted as-is.
    """
    nonempty = cast(BoolMatrix, vals.astype(bool))
    header_ends: set[int] = set()
    for j, c_bolds in enumerate(bolds.T):
        end = _header_end(c_bolds)
        if end < 0:
            continue

        # 1. plain content above a bold cell => bold marks sections/labels, not a header
        plain = nonempty[:, j] & ~c_bolds
        if plain.any() and c_bolds[int(plain.argmax()) + 1 :].any():
            return True

        header_ends.add(end)

    # 2. headers of differing height => values would start at differing rows
    return len(header_ends) > 1


def split_header_values(
    vals: StrMatrix, bolds: BoolMatrix
) -> tuple[list[str], StrMatrix]:
    """Split the input matrix into header values and corresponding data columns based on bold indicators.

    Args:
        vals (StrMatrix): The string matrix containing the data.
        bolds (BoolMatrix): The boolean matrix indicating bold positions.

    Returns:
        tuple[list[str], StrMatrix]: A tuple containing the extracted column headers and the resulting data matrix.
    """
    cols_headers: list[str] = []
    cols_values: list[np.ndarray[tuple[int], np.dtype[np.str_]]] = []
    max_values_length = 0
    if _no_classic_table(vals, bolds):
        return [""] * vals.shape[1], vals
    for j, c_bolds in enumerate(bolds.T):
        start = c_bolds.argmax()
        # no bolds => no header
        if start == 0 and not c_bolds[0]:
            cols_headers.append("")

            # this removes leading empty cells, which were there
            # hopefully to pad the column so that its values are on
            # equal height with columns with header
            mask = vals[:, j].astype(bool)
            start_idx = mask.argmax()

            col_v = vals[start_idx:, j]
            cols_values.append(col_v)
            max_values_length = max(max_values_length, len(col_v))
        else:
            end = start + c_bolds[start:].argmin()
            header = " ".join(vals[start:end, j])
            cols_headers.append(header)
            col_v = vals[end:, j]
            cols_values.append(col_v)
            max_values_length = max(max_values_length, len(col_v))

    out_val_cols = np.full((vals.shape[1], max_values_length), "", dtype=object)
    for j, col in enumerate(cols_values):
        out_val_cols[j, : len(col)] = col

    return cols_headers, out_val_cols.T


def sheet_to_headers_values(
    sheet: ReadOnlyWorksheet,
) -> tuple[Sequence[str], StrMatrix]:
    """Converts a worksheet into a Sequence of headers and a Sequence of value rows.

    Args:
        sheet (ReadOnlyWorksheet): The worksheet to process

    Returns:
        tuple[Sequence[str], StrMatrix]: A tuple containing the extracted headers and output value columns
    """
    vals, bolds, formats = sheet_to_matrices(sheet)
    _merge_cells_excel(sheet, vals)
    apply_format(vals, formats)
    vals, bolds = filter_empty_fill_none(vals, bolds)
    # is StrMatrix after apply_format and filter_empty_fill_none
    headers, out_val_cols = split_header_values(cast(StrMatrix, vals), bolds)
    return headers, out_val_cols


def sheet_to_markdown(sheet: ReadOnlyWorksheet) -> str:
    headers, out_val_cols = sheet_to_headers_values(sheet)
    return assemble_table(sheet.title, headers, out_val_cols.tolist())


def excel_to_sheets(path: Path) -> Iterable[ReadOnlyWorksheet]:
    wb = openpyxl.load_workbook(path, data_only=True, read_only=True)
    yield from wb.worksheets


def to_markdown(path: Path) -> str:
    tables = [sheet_to_markdown(sheet) for sheet in excel_to_sheets(path)]
    return "\n\n".join(tables)
