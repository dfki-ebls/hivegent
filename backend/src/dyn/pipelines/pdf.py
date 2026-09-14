from pathlib import Path
from tempfile import TemporaryDirectory

from dyn.serialization.complex.pdf import Converter

from .markdown import to_chunks as md_to_chunks


def to_chunks(inpath: Path, limit: int = 2048) -> list[str]:
    with TemporaryDirectory() as tmpdir:
        conv = Converter(inpath, Path(tmpdir))
        outp = conv.convert()
        return md_to_chunks(outp, limit)
