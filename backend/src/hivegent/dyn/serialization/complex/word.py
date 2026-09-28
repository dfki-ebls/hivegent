import re
import warnings
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import cast, final

from docx import Document
from docx.document import Document as _Document
from docx.drawing import Drawing
from docx.image.exceptions import UnrecognizedImageError
from docx.oxml.document import CT_Document
from docx.oxml.numbering import CT_NumPr
from docx.oxml.table import CT_Tbl
from docx.oxml.text.paragraph import CT_P
from docx.table import Table, _Cell  # pyright: ignore[reportPrivateUsage]
from docx.text.hyperlink import Hyperlink
from docx.text.paragraph import Paragraph
from docx.text.run import Run

from hivegent.dyn.commons.tabular_data import assemble_table
from hivegent.dyn.util import convert_office_legacy


def _escape_markdown_line_starters(text: str, escape_first_line: bool = True) -> str:
    """LLM-GENERATED.
    Escapes characters at the start of lines that have special markdown meaning.
    Handles: # headings, - * + list items, = - setext headings,
             > blockquotes, | tables, ~ ` fences, digits for ordered lists.

    Set escape_first_line=False when the text does not start a line in the output
    (e.g. a run in the middle of a paragraph), so no redundant escapes are added.

    Args:
        text (str): The input text
        escape_first_line (bool): Flag to control escaping of the first line

    Returns:
        str: The escaped string
    """
    # Characters that have special meaning at the start of a line
    # Order matters: multi-char patterns before single-char ones
    patterns = [
        # Ordered lists: "1." or "1)"
        (r"^(\d+)([.)]) ", r"\1\\\2 "),
        # ATX Headings: # ## ### etc.
        (r"^(#{1,6})( |$)", r"\\\1\2"),
        # Blockquotes
        (r"^(>)", r"\\\1"),
        # Setext-style heading underlines / thematic breaks: === or ---
        (r"^(={3,})", r"\\\1"),
        (r"^(-{3,})", r"\\\1"),
        # Fenced code blocks: ``` or ~~~
        (r"^(`{3,})", r"\\\1"),
        (r"^(~{3,})", r"\\\1"),
        # Unordered list items: - * + followed by a space
        (r"^([-*+]) ", r"\\\1 "),
        # Table rows
        (r"^(\|)", r"\\\1"),
    ]

    escaped_lines: list[str] = []
    for i, line in enumerate(text.splitlines()):
        if i == 0 and not escape_first_line:
            escaped_lines.append(line)
            continue
        for pattern, replacement in patterns:
            new_line = re.sub(pattern, replacement, line)
            if new_line != line:
                line = new_line
                break  # Only apply the first matching pattern per line
        escaped_lines.append(line)

    result = "\n".join(escaped_lines)
    # Preserve trailing newline if original had one
    if text.endswith("\n"):
        result += "\n"
    return result


def _iter_block_items(parent: _Document) -> Iterable[Paragraph | Table]:
    """LLM-GENERATED.
    Yield each Paragraph and Table child within the parent in document flow order.

    Args:
        parent (_Document): The parent document

    Returns:
        Iterator[Paragraph | Table]: An iterator of Paragraphs and Tables
    """
    body = cast(CT_Document, parent.element).body

    for child in body.iterchildren():
        if isinstance(child, CT_P):
            yield Paragraph(child, parent)
        elif isinstance(child, CT_Tbl):
            yield Table(child, parent)


@final
@dataclass(frozen=True)
class Converter:
    inpath: Path
    outpath: Path

    @property
    def drawing_path(self) -> Path:
        drawing_base = self.outpath if self.outpath.is_dir() else self.outpath.parent
        return drawing_base / f"{self.inpath.stem}_images"

    def handle_drawing(self, drawing: Drawing) -> str:
        """Extracts Word drawings/images.

        Args:
            drawing (Drawing): Drawing

        Returns:
            str: File reference string
        """
        if not drawing.has_picture:
            return ""
        try:
            img = drawing.image
        except UnrecognizedImageError:
            # legacy embedded formats python-docx has no header parser for (WMF,
            # EMF): skip the image rather than lose the whole document over it
            warnings.warn(
                f"{self.inpath.name}: skipped an image in an unrecognized format"
            )
            return ""
        self.drawing_path.mkdir(parents=True, exist_ok=True)
        save_path = self.drawing_path / f"{img.sha1}.{img.ext}"
        _ = save_path.write_bytes(img.blob)

        # relative to the output directory, next to the written markdown
        return f"![]({self.drawing_path.name}/{save_path.name})"

    def _render_run(self, run: Run) -> str:
        """Render the content of a run text object into a formatted Markdown string.

        Args:
            run (Run): The run object

        Returns:
            str: The rendered text
        """
        text = ""
        for elem in run.iter_inner_content():
            if isinstance(elem, str):
                text += elem
            elif isinstance(elem, Drawing):
                text += self.handle_drawing(elem)
            # ignore page breaks

        text = _escape_markdown_line_starters(text)
        if run.bold:
            text = " **" + text.strip() + "** "
        if run.italic:
            text = " _" + text.strip() + "_ "
        return text

    def _render_hyperlink(self, link: Hyperlink) -> str:
        """Construct a formatted Markdown string for a hyperlink.

        Args:
            link (Hyperlink): The hyperlink object

        Returns:
            str: The rendered hyperlink string
        """
        return f"[{self._render_paragraph_contents(link.runs)}]({link.address})"

    def is_list_item(self, paragraph: Paragraph) -> bool:
        """LLM-GENERATED.
        Return whether a paragraph carries list formatting.
        There is no nice API for this, so read the low level `w:numPr` properties off the
        paragraph element.

        Args:
            paragraph (Paragraph): The paragraph to inspect

        Returns:
            bool: True if the paragraph is a list item
        """
        p = cast(CT_P, paragraph.paragraph_format.element)
        # `xpath()` is typed as `Any`, so cast to what we know the result to be
        return bool(cast(list[CT_NumPr], p.xpath("./w:pPr/w:numPr")))

    def _render_paragraph(self, paragraph: Paragraph) -> str:
        """Render the paragraph content into a formatted Markdown string.

        Args:
            paragraph (Paragraph): The paragraph object

        Returns:
            str: The rendered string
        """
        if paragraph.style:
            style_id = paragraph.style.style_id
            if "Heading" in style_id:
                try:
                    level = int(style_id[7:])
                    return "#" * level + " " + paragraph.text
                # Fallback if non-numbered heading: Level 1
                except ValueError:
                    if len(paragraph.text.strip()) > 0:
                        return "# " + paragraph.text
            elif self.is_list_item(paragraph):
                return "- " + self._render_paragraph_contents(
                    paragraph.iter_inner_content()
                )
        return self._render_paragraph_contents(paragraph.iter_inner_content())

    def _render_paragraph_contents(self, contents: Iterable[Run | Hyperlink]) -> str:
        """Render the contents of a paragraph into a single string.

        Args:
            contents (Iterable[Run | Hyperlink]): contents

        Returns:
            str: The concatenated rendered string
        """
        rendered = [
            self._render_run(c) if isinstance(c, Run) else self._render_hyperlink(c)
            for c in contents
        ]
        return "".join(rendered)

    def _render_cell(self, cell: _Cell) -> str:
        """Return the concatenated and cleaned Markdown string representation of the cell's inner content.

        Args:
            cell (_Cell): The cell object

        Returns:
            str: The rendered cell content as a single string
        """
        raw_cells = [self._render_element(e) for e in cell.iter_inner_content()]
        cells_cleaned = [re.sub(r"\s+", " ", text) for text in raw_cells]
        return "".join(cells_cleaned)

    def _render_table(self, table: Table) -> str:
        """Render the table structure into a Markdown string.

        Args:
            table (Table): Table

        Returns:
            str: The rendered table as a string
        """

        rows = [
            [self._render_cell(cell).strip() for cell in row.cells]
            for row in table.rows
        ]
        if not rows:
            return ""

        # if the first row is the header, use it as such
        if rows[0] and all(text.count("*") == 4 for text in rows[0]):
            return assemble_table("", rows[0], rows[1:])

        # otherwise emit an empty header row so the Markdown table stays valid
        return assemble_table("", ["" for _ in rows[0]], rows)

    def _render_element(self, elem: Paragraph | Table | Run) -> str:
        """Routes an element to the appropriate Markdown renderer.

        Args:
            elem (Paragraph | Table | Run): The element to render

        Returns:
            str: The rendered string
        """
        ret = ""
        if isinstance(elem, Paragraph):
            ret = self._render_paragraph(elem)
        elif isinstance(elem, Table):
            ret = self._render_table(elem)
        else:
            ret = self._render_run(elem)
        return ret

    def _to_markdown(self) -> str:
        """Return the rendered content of a DOCX file as a Markdown string.

        Returns:
            str: Return the rendered document content as a Markdown string
        """
        with convert_office_legacy(self.inpath, "docx") as docx_path:
            doc = Document(str(docx_path))
            elems = _iter_block_items(doc)
            rendered = [r for e in elems if (r := self._render_element(e)).strip()]
        return "\n\n".join(rendered)

    def convert(self) -> Path:
        """Convert the input file to a Markdown file.

        Returns:
            Path: The resulting file path
        """
        target = (
            self.outpath / f"{self.inpath.stem}.md"
            if self.outpath.is_dir()
            else self.outpath
        )
        _ = target.write_text(self._to_markdown())
        return target
