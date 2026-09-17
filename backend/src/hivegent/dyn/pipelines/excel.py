from pathlib import Path

from hivegent.dyn.segmentation.tabular_data import excel_to_markdown_chunks
from hivegent.dyn.util import convert_office_legacy


def to_chunks(inpath: Path, limit: int = 2048) -> list[str]:
    with convert_office_legacy(inpath, "xlsx") as p:
        return excel_to_markdown_chunks(p, limit)
