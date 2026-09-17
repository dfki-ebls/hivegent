from pathlib import Path

from hivegent.dyn.segmentation.tabular_data import json_to_markdown_chunks


def to_chunks(inpath: Path, limit: int = 2048) -> list[str]:
    return json_to_markdown_chunks(inpath, limit)
