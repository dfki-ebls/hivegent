from hivegent.llm_config import LlmConfig
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
from hivegent.dyn.serialization.complex.word import Converter as WordConverter
from hivegent.dyn.serialization.simple.email import to_markdown as email_to_markdown
from hivegent.dyn.serialization.simple.excel import to_markdown as excel_to_markdown
from hivegent.dyn.serialization.simple.json import to_markdown as json_to_markdown
from hivegent.subprocesses.libreoffice import libreoffice_convert

from .base import (
    ConversionResult,
    DocumentConverter,
    collect_dir_images,
    ExtractedImage,
)

__all__ = ["DynAutoConverter"]


class DynAutoConfig(BaseModel):
    """Configuration for the LLM conversion pipeline."""

    summarize_floats: bool = Field(
        default=True, description="Use the LLM to summarize figures and tables?"
    )


async def _convert_markdown(
    path: Path,
    /,
    summarize_floats: bool,
    llm_options: LlmConfig | None = None,
    images: dict[str, ExtractedImage] | None = None,
) -> ConversionResult:
    doc = await parse(path, summarize_floats, llm_options)
    # convert TableNode to TableMdNode
    for _, contentlist in doc.tree:
        for node in contentlist:
            if isinstance(node.data, (TableNode, TableMdNode)):
                table = assemble_table(
                    node.data.name, node.data.headers, node.data.vals
                )
                node.data = TextNode(content=table)
    text = doc.markdown
    return ConversionResult(markdown=text)


async def _convert_word(
    path: Path, convert: bool, summarize_floats: bool, llm_options: LlmConfig | None
) -> ConversionResult:
    with TemporaryDirectory() as tmpdir:
        if convert:
            converted = await libreoffice_convert(path, Path(tmpdir), to="docx")
            assert converted is not None
            path = converted
        converter = WordConverter(path, Path(tmpdir))
        md_path = converter.convert()
        # the images live in tmpdir, so read them before it is removed
        images = collect_dir_images(converter.drawing_path, Path(tmpdir))
        return await _convert_markdown(md_path, summarize_floats, llm_options, images)


async def _convert_email(path: Path, /) -> ConversionResult:
    subject, body = email_to_markdown(path)
    text = f"# {subject}\n\n{body}"
    return ConversionResult(markdown=text)


async def _convert_excel(path: Path, convert: bool) -> ConversionResult:
    with TemporaryDirectory() as tmpdir:
        if convert:
            converted = await libreoffice_convert(path, Path(tmpdir), to="xlsx")
            assert converted is not None
            path = converted
        text = excel_to_markdown(path)
    return ConversionResult(markdown=text)


async def _convert_json(path: Path, /) -> ConversionResult:
    text = json_to_markdown(path)
    return ConversionResult(markdown=text)


@dataclass(slots=True, frozen=True)
class DynAutoConverter(DocumentConverter):
    name = "dynauto"
    config: DynAutoConfig = field(default_factory=DynAutoConfig)
    llm_options: LlmConfig | None = None

    async def _convert(self, path: Path, /) -> ConversionResult:
        match path.suffix:
            case ".md":
                return await _convert_markdown(
                    path, self.config.summarize_floats, self.llm_options
                )
            case ".doc":
                return await _convert_word(
                    path, True, self.config.summarize_floats, self.llm_options
                )
            case ".docx":
                return await _convert_word(
                    path, False, self.config.summarize_floats, self.llm_options
                )
            case ".eml" | ".txt":
                return await _convert_email(path)
            case ".xls":
                return await _convert_excel(path, convert=True)
            case ".xlsx":
                return await _convert_excel(path, convert=False)
            case ".json" | ".jsonl":
                return await _convert_json(path)
            case _:
                raise ValueError(
                    f"Conversion of file type {path.suffix} is not implemented!"
                )
