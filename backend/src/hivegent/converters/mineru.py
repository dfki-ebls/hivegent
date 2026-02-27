"""MinerU-based document converter."""

import asyncio
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from magic_pdf.data.data_reader_writer import FileBasedDataWriter  # type: ignore[import-not-found]  # pyright: ignore[reportMissingImports]
from magic_pdf.pipe.UNIPipe import UNIPipe  # type: ignore[import-not-found]  # pyright: ignore[reportMissingImports]
from pydantic import BaseModel

from .base import DocumentConverter

__all__ = ["MinerUConverter", "MinerUConverterConfig"]


class MinerUConverterConfig(BaseModel):
    """Configuration for the MinerU conversion pipeline."""


# MinerU has no public format listing API.
# https://github.com/opendatalab/MinerU#supported-file-types
@dataclass(slots=True, frozen=True)
class MinerUConverter(DocumentConverter):
    """Document converter using the MinerU library.

    MinerU provides high-quality PDF parsing and document conversion. Note that
    MinerU does NOT support XLSX files.
    """

    name = "mineru"
    extensions = frozenset(
        {
            ".pdf",
            ".docx",
            ".pptx",
            ".png",
            ".jpg",
            ".jpeg",
        }
    )

    def _convert_sync(self, path: Path) -> str:
        """Run the synchronous MinerU conversion."""
        pdf_bytes = path.read_bytes()

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)

            image_writer = FileBasedDataWriter(str(temp_path / "images"))

            pipe = UNIPipe(
                pdf_bytes,
                [],  # model_list - empty for auto-detection
                image_writer,
            )

            pipe.pipe_classify()
            pipe.pipe_analyze()
            pipe.pipe_parse()

            md_content = pipe.pipe_mk_markdown(
                str(temp_path / "images"),
                drop_mode="none",
            )

            return str(md_content)

    async def __call__(
        self,
        path: Path,
        /,
        config: dict[str, Any] | None = None,
    ) -> str:
        """Convert a document to markdown using MinerU.

        Args:
            path: Path to the document to convert.
            config: Optional pipeline configuration (currently unused).

        Returns:
            The document content converted to markdown.
        """
        return await asyncio.to_thread(self._convert_sync, path)
