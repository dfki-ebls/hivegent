"""Table document converter using chonkie's TableChef."""

import asyncio
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import ClassVar

from chonkie import TableChef

from .base import ConversionResult, DocumentConverter

__all__ = ["ChonkieTableConverter"]


@lru_cache(maxsize=4)
def _build_chef() -> TableChef:
    return TableChef()


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

    async def _convert(self, path: Path, /) -> ConversionResult:
        doc = await asyncio.to_thread(_build_chef().process, path)
        return ConversionResult(markdown=doc.content)
