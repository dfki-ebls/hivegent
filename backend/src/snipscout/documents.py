"""Document loading and BM25 indexing utilities."""

import bm25s

from .config import FileExtension, settings

__all__ = [
    "create_index",
    "get_cached_documents",
    "load_documents",
    "reload_documents",
    "search_documents",
]

# Module-level cache
_documents: dict[str, str] = {}
_index: bm25s.BM25 | None = None
_filenames: list[str] = []


def load_documents() -> dict[str, str]:
    """Load all supported files from the data directory.

    Returns:
        Dict mapping filename to content.
    """
    documents: dict[str, str] = {}
    data_dir = settings.data_dir
    if not data_dir.exists():
        return documents

    for ext in FileExtension:
        for file_path in sorted(data_dir.glob(f"*{ext}")):
            documents[file_path.name] = file_path.read_text(encoding="utf-8")

    return documents


def reload_documents() -> None:
    """Reload documents from disk and rebuild the BM25 index."""
    global _documents, _index, _filenames
    _documents = load_documents()
    if _documents:
        _index, _filenames = create_index(_documents)
    else:
        _index = None
        _filenames = []


def get_cached_documents() -> tuple[dict[str, str], bm25s.BM25 | None, list[str]]:
    """Get the cached documents and index.

    Returns:
        Tuple of (documents dict, BM25 index or None, filenames list).
    """
    return _documents, _index, _filenames


def create_index(documents: dict[str, str]) -> tuple[bm25s.BM25, list[str]]:
    """Create a BM25 index from documents.

    Args:
        documents: Dict mapping filename to content.

    Returns:
        Tuple of (BM25 index, list of filenames in index order).
    """
    filenames = list(documents.keys())
    corpus = [documents[f] for f in filenames]
    corpus_tokens = bm25s.tokenize(corpus)
    retriever = bm25s.BM25()
    retriever.index(corpus_tokens)
    return retriever, filenames


def search_documents(
    query: str,
    documents: dict[str, str],
    index: bm25s.BM25,
    filenames: list[str],
    top_k: int = 3,
) -> list[tuple[str, str, float]]:
    """Search documents using BM25.

    Args:
        query: Search query string.
        documents: Dict mapping filename to content.
        index: BM25 index.
        filenames: List of filenames in index order.
        top_k: Number of results to return.

    Returns:
        List of (filename, content, score) tuples sorted by relevance.
    """
    if not documents:
        return []

    query_tokens = bm25s.tokenize([query])
    indices, scores = index.retrieve(query_tokens, k=min(top_k, len(documents)))

    results: list[tuple[str, str, float]] = []
    for idx, score in zip(indices[0], scores[0]):
        if idx < len(filenames):
            filename = filenames[idx]
            results.append((filename, documents[filename], float(score)))
    return results


# Initialize cache on module load
reload_documents()
