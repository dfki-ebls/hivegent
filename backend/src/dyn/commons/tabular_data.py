from collections.abc import Iterable, Sequence
from typing import cast

import numpy as np
from bs4 import BeautifulSoup
from bs4.element import Tag

type ObjMatrix = np.ndarray[tuple[int, int], np.dtype[np.object_]]
type BoolMatrix = np.ndarray[tuple[int, int], np.dtype[np.bool_]]
type StrMatrix = np.ndarray[tuple[int, int], np.dtype[np.str_]]
type MergeRange = tuple[int, int, int, int]


def render_row(vals: Iterable[str]) -> str:
    """Renders a single row of values in Markdown table syntax.

    Args:
        vals (Iterable[str]): The iterable of strings to join

    Returns:
        str: A pipe-separated row string
    """
    return "| " + " | ".join(vals) + " |"


def assemble_table(
    title: str,
    headers: Sequence[str],
    vals: Iterable[Iterable[str]],
    add_title: bool = True,
) -> str:
    """Construct the Markdown-formatted string representation of a table.

    Args:
        title (str): The title of the table
        headers (Sequence[str]): The column headers
        vals (Iterable[Iterable[str]]): The data values for the table
        add_title (bool): Flag to determine if the title is included

    Returns:
        str: The assembled table as a string
    """
    seps = ["---" for _ in range(len(headers))]
    headers = [f"**{h}**" if h else "" for h in headers]
    if not title.startswith("#"):
        title = f"# {title}".rstrip()
    rendered_rows = [title, "", render_row(headers), render_row(seps)]
    if not add_title:
        rendered_rows = rendered_rows[2:]
    rendered_rows += [render_row(row) for row in vals]
    return "\n".join(rendered_rows)


type Cell = tuple[str, int, int]  # (text, rowspan, colspan)


def _span(tag: Tag, attr: str) -> int:
    """Reads HTML table cell's rowspan / colspan attributes and returns it.

    Args:
        tag (Tag): The tag object to inspect
        attr (str): The attribute key to retrieve

    Returns:
        int: The resulting span value
    """
    try:
        n = int(str(tag.get(attr, str(1))))
    except (TypeError, ValueError):
        return 1
    return n if n > 0 else 1


def _text(tag: Tag) -> str:
    """Formats the text of a single HTML table cell (th or td).

    Args:
        tag (Tag): Tag

    Returns:
        str: The extracted text
    """
    text = tag.get_text(" ", strip=True)
    if tag.name == "th" and text:
        return f"**{text}**"
    return text


def merge_cell(bounds: MergeRange, vals: ObjMatrix):
    """Merge a cell in the matrix using specified bounds.

    Args:
        bounds (MergeRange): Defines the area to merge
        vals (ObjMatrix): The matrix to modify

    Returns:
        None: None
    """
    origin_val = cast(object, vals[bounds[1], bounds[0]])
    row_low = bounds[1]
    row_high = bounds[3] + 1
    col_low = bounds[0]
    col_high = bounds[2] + 1
    vals[row_low:row_high, col_low:col_high] = origin_val


def _html_to_objects_matrix(rows: Sequence[Sequence[Cell]]) -> ObjMatrix:
    """LLM-GENERATED. Create a table matrix from a sequence of html table rows.

    Args:
        rows (Sequence[Sequence[Cell]]): The input rows defining the structure

    Returns:
        np.ndarray: A matrix representing the parsed cell values
    """

    width = 0
    carry: dict[int, int] = {}  # col -> rows still occupied, counting the current row
    for row in rows:
        col = 0
        next_carry = {c: n - 1 for c, n in carry.items() if n > 1}
        for _, rowspan, colspan in row:
            while col in carry:
                col += 1
            if rowspan > 1:
                for c in range(col, col + colspan):
                    next_carry[c] = rowspan - 1
            col += colspan
        width = max(width, col)
        carry = next_carry

    vals = np.full((len(rows), width), "", dtype=object)
    occupied = np.zeros((len(rows), width), dtype=bool)
    for i, row in enumerate(rows):
        col = 0
        for value, rowspan, colspan in row:
            while col < width and occupied[i, col]:
                col += 1
            if col >= width:
                break
            r_hi = min(i + rowspan, len(rows))
            c_hi = min(col + colspan, width)
            vals[i:r_hi, col:c_hi] = value  # rows first, then columns
            occupied[i:r_hi, col:c_hi] = True
            col = c_hi
    return vals


def _header_depth(rows: Sequence[Sequence[Cell]]) -> int:
    """LLM-GENERATED. Number of leading rows belonging to the header block.

    A rowspan on a first-row cell covers the rows below it, so those rows are
    part of the header too: a "Method" label with rowspan 2 sits beside colspan
    groups that are subdivided on the next row. At least one row is left over
    as the body.
    """
    depth = max((rowspan for _, rowspan, _ in rows[0]), default=1)
    return max(1, min(depth, len(rows) - 1))


def _join_header(cells: Iterable[object]) -> str:
    """LLM-GENERATED. Collapse the header rows of one column into a single label.

    Consecutive repeats come from a rowspan covering the column and are kept
    once, so "Method"/"Method" stays "Method" while "Image Resolution (512 ×
    512)"/"MSE" becomes "Image Resolution (512 × 512) MSE".
    """
    parts: list[str] = []
    for cell in cells:
        text = str(cell).strip()
        if text and (not parts or parts[-1] != text):
            parts.append(text)
    return " ".join(parts)


def html_to_headers_rows(html: str) -> tuple[Sequence[str], Sequence[Sequence[str]]]:
    """Converts a HTML table into a header, rows representation.

    Args:
        html (str): The HTML content to process

    Returns:
        tuple[Sequence[str], Sequence[Sequence[str]]: A tuple containing header rows and the remaining table data
    """
    soup = BeautifulSoup(html, "html.parser")
    table = soup.find("table")
    if table is None:
        raise ValueError("No table found!")

    rows = [
        [
            (_text(c), _span(c, "rowspan"), _span(c, "colspan"))
            for c in tr.find_all(["td", "th"], recursive=False)
        ]
        for tr in table.find_all("tr")
        if tr.find_parent("table") is table
    ]
    matrix = _html_to_objects_matrix(rows)
    if len(rows) < 2:
        return [], matrix.tolist()

    depth = _header_depth(rows)
    headers = [_join_header(matrix[:depth, j]) for j in range(matrix.shape[1])]
    return headers, matrix[depth:].tolist()
