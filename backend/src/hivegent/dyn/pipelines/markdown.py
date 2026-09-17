from pathlib import Path

from hivegent.dyn.segmentation.complex import full_chunking
from hivegent.dyn.segmentation.complex import to_chunks as complex_to_chunks
from hivegent.dyn.serialization.complex.markdown import parse


def to_chunks(inpath: Path, limit: int = 2048) -> list[str]:
    doc = parse(inpath)
    full_chunking(doc, limit)
    return complex_to_chunks(doc, limit)
