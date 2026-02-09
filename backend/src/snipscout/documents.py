"""Document loading and BM25 indexing utilities."""

from dataclasses import dataclass
from pathlib import Path

import bm25s
from frozendict import frozendict

from .config import TEXT_EXTENSIONS, settings

__all__ = [
    "DocumentCache",
    "SearchResult",
    "get_cached_documents",
    "reload_user_documents",
    "search_documents",
]


@dataclass(slots=True, frozen=True)
class SearchResult:
    """A single document search result."""

    filename: str
    content: str
    score: float


@dataclass(slots=True, frozen=True)
class DocumentCache:
    """Cached documents and BM25 index for a directory."""

    documents: frozendict[str, str]
    index: bm25s.BM25 | None = None


_EMPTY = DocumentCache(documents=frozendict())

# Cache keyed by directory path
_caches: dict[Path, DocumentCache] = {}


def _build_cache(path: Path) -> DocumentCache:
    """Load documents from disk and build BM25 index."""
    if not path.exists():
        return _EMPTY

    documents: dict[str, str] = {}
    for ext in TEXT_EXTENSIONS:
        for file_path in sorted(path.glob(f"*{ext}")):
            if file_path.is_file():
                documents[file_path.name] = file_path.read_text(encoding="utf-8")

    if not documents:
        return _EMPTY

    corpus_tokens = bm25s.tokenize(list(documents.values()))
    retriever = bm25s.BM25()
    retriever.index(corpus_tokens)
    return DocumentCache(documents=frozendict(documents), index=retriever)


def get_cached_documents(path: Path) -> DocumentCache:
    """Get cached documents for a directory, loading on first access.

    Args:
        path: Directory to get documents for.

    Returns:
        DocumentCache with documents and BM25 index.
    """
    if path not in _caches:
        _caches[path] = _build_cache(path)
    return _caches[path]


def reload_user_documents(user_id: str) -> None:
    """Reload documents from disk and rebuild the cache for a user.

    Args:
        user_id: The user ID to reload documents for.
    """
    path = settings.get_user_documents_dir(user_id)
    _caches[path] = _build_cache(path)


def search_documents(
    cache: DocumentCache,
    query: str,
    top_k: int = 3,
) -> list[SearchResult]:
    """Search documents using BM25.

    Args:
        cache: The document cache to search.
        query: Search query string.
        top_k: Number of results to return.

    Returns:
        List of SearchResult sorted by relevance.
    """
    if not cache.documents or cache.index is None:
        return []

    filenames = list(cache.documents.keys())
    query_tokens = bm25s.tokenize([query])
    indices, scores = cache.index.retrieve(
        query_tokens, k=min(top_k, len(cache.documents))
    )

    results: list[SearchResult] = []
    for idx, score in zip(indices[0], scores[0]):
        if idx < len(filenames):
            filename = filenames[idx]
            results.append(
                SearchResult(
                    filename=filename,
                    content=cache.documents[filename],
                    score=float(score),
                )
            )
    return results
