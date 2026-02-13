"""Document loading and BM25 search utilities."""

from dataclasses import dataclass
from pathlib import Path

import bm25s

from .config import TEXT_EXTENSIONS

__all__ = [
    "SearchResult",
    "load_documents",
    "search_documents",
]


@dataclass(slots=True, frozen=True)
class SearchResult:
    """A single document search result."""

    filename: str
    content: str
    score: float


def load_documents(path: Path) -> dict[str, str]:
    """Load all documents from a directory.

    Args:
        path: Directory containing text documents.

    Returns:
        Dict mapping relative filename to file content.
    """
    if not path.exists():
        return {}

    documents: dict[str, str] = {}
    for ext in TEXT_EXTENSIONS:
        for file_path in sorted(path.rglob(f"*{ext}")):
            if file_path.is_file():
                key = str(file_path.relative_to(path).as_posix())
                documents[key] = file_path.read_text(encoding="utf-8")

    return documents


def search_documents(
    path: Path,
    query: str,
    top_k: int = 3,
) -> list[SearchResult]:
    """Search documents using a temporary BM25 index.

    Loads all documents from disk, builds a BM25 index, searches, and
    discards the index afterwards.

    Args:
        path: Directory containing text documents.
        query: Search query string.
        top_k: Number of results to return.

    Returns:
        List of SearchResult sorted by relevance.
    """
    documents = load_documents(path)
    if not documents:
        return []

    filenames = list(documents.keys())
    corpus_tokens = bm25s.tokenize(list(documents.values()))
    retriever = bm25s.BM25()
    retriever.index(corpus_tokens)

    query_tokens = bm25s.tokenize([query])
    indices, scores = retriever.retrieve(
        query_tokens, k=min(top_k, len(documents))
    )

    results: list[SearchResult] = []
    for idx, score in zip(indices[0], scores[0]):
        if idx < len(filenames):
            filename = filenames[idx]
            results.append(
                SearchResult(
                    filename=filename,
                    content=documents[filename],
                    score=float(score),
                )
            )
    return results
