from dataclasses import dataclass, field
from pathlib import Path
from tempfile import TemporaryDirectory

from pydantic import BaseModel, Field

from hivegent.dyn.commons.tabular_data import assemble_table
from hivegent.dyn.serialization.complex.markdown import parse
from hivegent.dyn.serialization.complex.markdown.model import (
    TableMdNode,
    TableNode,
    TextNode,
)
from hivegent.dyn.serialization.complex.pdf import Converter as PDFConverter
from hivegent.dyn.serialization.complex.word import Converter as WordConverter
from hivegent.dyn.serialization.simple.email import to_markdown as email_to_markdown
from hivegent.dyn.serialization.simple.excel import to_markdown as excel_to_markdown
from hivegent.dyn.serialization.simple.json import to_markdown as json_to_markdown
from hivegent.dyn.serialization.web import to_md as web_to_markdown

from .base import ConversionResult, DocumentConverter

__all__ = [
    "DynMarkdownConfig",
    "DynMarkdownConverter",
    "DynPDFConverter",
    "DynWordConverter",
    "DynEmailConverter",
    "DynExcelConverter",
    "DynJSONConverter",
    "DynWebConverter",
]


class DynMarkdownConfig(BaseModel):
    """Configuration for the LLM conversion pipeline."""

    summarize_floats: bool = Field(default=True)


@dataclass(slots=True, frozen=True)
class DynMarkdownConverter(DocumentConverter):
    """Optimizes a Markdown file to facilitate the generation of more independent chunks"""

    name = "dynmarkdown"
    config: DynMarkdownConfig = field(default_factory=DynMarkdownConfig)

    async def _convert(self, path: Path, /) -> ConversionResult:
        doc = parse(path)
        # convert TableNode to TableMdNode
        for _, contentlist in doc.tree:
            for node in contentlist:
                if isinstance(node.data, (TableNode, TableMdNode)):
                    # TODO: fix line numbers
                    table = assemble_table(
                        node.data.name, node.data.headers, node.data.vals
                    )
                    node.data = TextNode(content=table)
        text = doc.markdown
        return ConversionResult(markdown=text)


@dataclass(slots=True, frozen=True)
class DynPDFConverter(DocumentConverter):
    """Converts a PDF file to Markdown to facilitate the generation of more independent chunks. Currently deactivated."""

    name = "dynpdf"

    async def _convert(self, path: Path, /) -> ConversionResult:
        with TemporaryDirectory() as tmpdir:
            converter = PDFConverter(path, Path(tmpdir))
            md_path = converter.convert()
            return ConversionResult(markdown=md_path.read_text())


@dataclass(slots=True, frozen=True)
class DynWordConverter(DocumentConverter):
    """Converts a Word file to Markdown to facilitate the generation of more independent chunks"""

    name = "dynword"

    async def _convert(self, path: Path, /) -> ConversionResult:
        with TemporaryDirectory() as tmpdir:
            converter = WordConverter(path, Path(tmpdir))
            md_path = converter.convert()
            return ConversionResult(markdown=md_path.read_text())


@dataclass(slots=True, frozen=True)
class DynEmailConverter(DocumentConverter):
    """Converts an email file (eml) to Markdown to facilitate the generation of more independent chunks"""

    name = "dynemail"

    async def _convert(self, path: Path, /) -> ConversionResult:
        subject, body = email_to_markdown(path)
        text = f"# {subject}\n\n{body}"
        return ConversionResult(markdown=text)


@dataclass(slots=True, frozen=True)
class DynExcelConverter(DocumentConverter):
    """Converts an Excel file to Markdown to facilitate the generation of more independent chunks"""

    name = "dynexcel"

    async def _convert(self, path: Path, /) -> ConversionResult:
        text = excel_to_markdown(path)
        return ConversionResult(markdown=text)


@dataclass(slots=True, frozen=True)
class DynJSONConverter(DocumentConverter):
    """Converts a JSON file to Markdown to facilitate the generation of more independent chunks"""

    name = "dynjson"

    async def _convert(self, path: Path, /) -> ConversionResult:
        text = json_to_markdown(path)
        return ConversionResult(markdown=text)


@dataclass(slots=True, frozen=True)
class DynWebConverter(DocumentConverter):
    """Converts a web source to Markdown to facilitate the generation of more independent chunks. Currently deactivated."""

    name = "dynweb"

    async def _convert(self, url: str, is_single: bool = True, /) -> ConversionResult:
        text = web_to_markdown(url, is_single)
        return ConversionResult(markdown=text)
