"""Document loading and BM25 indexing utilities."""

from dataclasses import dataclass, field

import bm25s

from .config import FileExtension, settings

__all__ = [
    "create_index",
    "get_user_documents",
    "load_documents",
    "reload_user_documents",
    "search_documents",
]


@dataclass
class UserDocumentCache:
    """Cache for a user's documents and BM25 index."""

    documents: dict[str, str] = field(default_factory=dict)
    index: bm25s.BM25 | None = None
    filenames: list[str] = field(default_factory=list)


# Per-user document cache
_user_caches: dict[str, UserDocumentCache] = {}


def load_documents(user_id: str) -> dict[str, str]:
    """Load all supported files from a user's data directory.

    Args:
        user_id: The user ID to load documents for.

    Returns:
        Dict mapping filename to content.
    """
    documents: dict[str, str] = {}
    data_dir = settings.get_user_documents_dir(user_id)
    if not data_dir.exists():
        return documents

    for ext in FileExtension:
        for file_path in sorted(data_dir.glob(f"*{ext}")):
            documents[file_path.name] = file_path.read_text(encoding="utf-8")

    return documents


def reload_user_documents(user_id: str) -> None:
    """Reload documents from disk and rebuild the BM25 index for a user.

    Args:
        user_id: The user ID to reload documents for.
    """
    documents = load_documents(user_id)
    cache = UserDocumentCache(documents=documents)

    if documents:
        cache.index, cache.filenames = create_index(documents)
    else:
        cache.index = None
        cache.filenames = []

    _user_caches[user_id] = cache


def get_user_documents(
    user_id: str,
) -> tuple[dict[str, str], bm25s.BM25 | None, list[str]]:
    """Get the cached documents and index for a user.

    Args:
        user_id: The user ID to get documents for.

    Returns:
        Tuple of (documents dict, BM25 index or None, filenames list).
    """
    if user_id not in _user_caches:
        reload_user_documents(user_id)
    cache = _user_caches[user_id]
    return cache.documents, cache.index, cache.filenames


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
