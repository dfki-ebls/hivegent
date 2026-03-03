"""Table document converter using chonkie's TableChef."""

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar

from chonkie import TableChef

from .base import DocumentConverter

__all__ = ["ChonkieTableConverter"]


@dataclass(slots=True, frozen=True)
class ChonkieTableConverter(DocumentConverter):
    """Converter that transforms CSV/Excel files to markdown tables.

    Uses chonkie's TableChef (backed by pandas) to parse tabular data
    and produce markdown table output.
    """

    name: ClassVar[str] = "table-chef"
    extensions: ClassVar[frozenset[str]] = frozenset({".csv", ".xls", ".xlsx"})

    async def __call__(
        self,
        path: Path,
        /,
    ) -> str:
        """Convert a tabular file to markdown.

        Args:
            path: Path to the CSV or Excel file.

        Returns:
            Markdown table content.
        """
        doc = await asyncio.to_thread(TableChef().process, path)
        return doc.content
