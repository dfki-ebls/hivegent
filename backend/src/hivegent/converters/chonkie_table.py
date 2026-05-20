"""Table document converter using chonkie's TableChef."""

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar

from chonkie import TableChef

from .base import ConversionResult, DocumentConverter

__all__ = ["ChonkieTableConverter"]


@dataclass(slots=True, frozen=True)
class ChonkieTableConverter(DocumentConverter):
    """Converter that transforms CSV/Excel files to markdown tables.

    Uses chonkie's TableChef (backed by pandas) to parse tabular data
    and produce markdown table output.
    """

    name: ClassVar[str] = "table-chef"
    label: ClassVar[str] = "Table Chef"
    description: ClassVar[str] = "CSV/Excel to markdown tables via pandas"
    extensions: ClassVar[frozenset[str]] = frozenset({".csv", ".xls", ".xlsx"})

    async def __call__(
        self,
        path: Path,
        /,
    ) -> ConversionResult:
        """Convert a tabular file to markdown.

        Args:
            path: Path to the CSV or Excel file.

        Returns:
            The conversion result with markdown table content.
        """
        doc = await asyncio.to_thread(TableChef().process, path)
        return ConversionResult(markdown=doc.content)
