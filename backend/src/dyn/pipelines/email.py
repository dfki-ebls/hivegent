from functools import lru_cache
from pathlib import Path

from dyn.segmentation.text import Chunker
from dyn.serialization.simple.email import to_markdown


@lru_cache(maxsize=4)
def get_chunker(limit: int) -> Chunker:
    """Cached per limit: building a Chunker loads a spaCy pipeline and a
    semantic chunker, which is wasteful (and memory-hungry) per document."""
    return Chunker(limit)


def to_chunks(
    path: Path, limit: int = 2048, chunker: Chunker | None = None
) -> list[str]:
    subject, email_md = to_markdown(path)
    if chunker is None:
        chunker = get_chunker(limit)
    chunks = chunker.divide_text(email_md)
    chunks = [f"# {subject}\n\n{chunk}" for chunk in chunks]
    return chunks
