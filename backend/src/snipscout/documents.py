"""Document loading and BM25 indexing utilities."""

from pathlib import Path

import bm25s

__all__ = ["load_documents", "create_index", "search_documents"]

DATA_DIR = Path("data")


def load_documents() -> dict[str, str]:
    """Load all .txt files from the data directory.

    Returns:
        Dict mapping filename to content.
    """
    documents: dict[str, str] = {}
    if not DATA_DIR.exists():
        return documents

    for txt_file in sorted(DATA_DIR.glob("*.txt")):
        documents[txt_file.name] = txt_file.read_text(encoding="utf-8")

    return documents


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
