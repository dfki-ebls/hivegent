"""MinerU-based document converter with lazy loading."""

import asyncio
import tempfile
from pathlib import Path
from typing import Any

from .base import DocumentConverter

__all__ = ["MinerUConverter"]


class MinerUConverter(DocumentConverter):
    """Document converter using the MinerU library.

    MinerU provides high-quality PDF parsing and document conversion. Note that
    MinerU does NOT support XLSX files. This converter uses lazy imports to
    avoid loading the heavy dependencies until needed.
    """

    @property
    def name(self) -> str:
        """The unique name of this converter."""
        return "mineru"

    def __init__(self) -> None:
        """Initialize the converter with lazy loading."""
        self._pipe_class: Any = None
        self._writer_class: Any = None

    def _convert_sync(self, file_path: Path) -> str:
        """Run the synchronous MinerU conversion.

        Raises:
            ImportError: If mineru is not installed.
        """
        if self._pipe_class is None:
            try:
                from magic_pdf.data.data_reader_writer import FileBasedDataWriter  # type: ignore[import-not-found]  # pyright: ignore[reportMissingImports]
                from magic_pdf.pipe.UNIPipe import UNIPipe  # type: ignore[import-not-found]  # pyright: ignore[reportMissingImports]

                self._pipe_class = UNIPipe
                self._writer_class = FileBasedDataWriter
            except ImportError as e:
                raise ImportError(
                    "mineru is not installed. Install with: pip install mineru"
                ) from e

        pdf_bytes = file_path.read_bytes()

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)

            image_writer = self._writer_class(str(temp_path / "images"))

            pipe = self._pipe_class(
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

    async def convert(self, file_path: Path) -> str:
        """Convert a document to markdown using MinerU.

        Args:
            file_path: Path to the document to convert.

        Returns:
            The document content converted to markdown.

        Raises:
            ImportError: If mineru is not installed.
        """
        return await asyncio.to_thread(self._convert_sync, file_path)
