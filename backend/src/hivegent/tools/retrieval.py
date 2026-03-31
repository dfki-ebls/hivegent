"""Retrieval tools using cbrkit indexed backends."""

import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Annotated, Literal, cast, override

import cbrkit
from pydantic import Field

from .base import Tool, apply_prefix

__all__ = [
    "IndexedStorage",
    "IndexedStorageFilterFunc",
    "LanceDBSearchTool",
    "SearchQueryArg",
    "SearchResult",
    "SearchMaxResultsArg",
    "SearchType",
    "SearchTypeArg",
]

logger = logging.getLogger(__name__)

type SearchType = Literal["dense", "sparse", "hybrid"]

IndexedStorageFilterFunc = Callable[[str], bool] | None
"""Optional predicate on result keys.

Receives the *unprefixed* key and should return ``True`` to keep the
result.
"""


@dataclass(slots=True, frozen=True)
class SearchResult:
    """A single search result with key, text, and relevance score."""

    key: str
    text: str
    score: float


@dataclass(slots=True, frozen=True)
class IndexedStorage:
    """A LanceDB storage with optional prefix and filter.

    Mirrors :class:`~tools.base.SearchPath` for vector retrieval:
    each storage may carry a display prefix and an independent filter
    predicate.

    Attributes:
        storage: The cbrkit LanceDB storage instance.
        prefix: Display prefix prepended to result keys from this
            storage.  ``None`` means no prefix.
        filter_func: Optional predicate on result keys.  Receives
            the *unprefixed* key and should return ``True`` to keep
            the result.
    """

    storage: cbrkit.indexable.lancedb[str]
    prefix: str | None = None
    filter_func: IndexedStorageFilterFunc = None

    def prefixed(self, key: str) -> str:
        """Return *key* with this storage's prefix prepended."""
        return apply_prefix(self.prefix, key)


SearchQueryArg = Annotated[
    str,
    Field(description="Natural language search query."),
]
SearchMaxResultsArg = Annotated[
    int,
    Field(description="Maximum number of results to return.", ge=1),
]
SearchTypeArg = Annotated[
    SearchType,
    Field(
        description=(
            "Retrieval strategy: `dense` for semantic similarity, `sparse` "
            "for keyword matching, and `hybrid` to combine both."
        ),
    ),
]


@dataclass(slots=True, frozen=True)
class LanceDBSearchTool[R = SearchResult](Tool):
    """Search one or more LanceDB storages using cbrkit indexed retrieval.

    Each :class:`IndexedStorage` is queried independently so that
    per-storage filters and prefixes are applied correctly.
    Results are merged and sorted by score.

    Args:
        storages: One or more indexed storage entries.
        result_mapper: Optional callable that transforms each
            :class:`SearchResult` before it is returned.  When ``None``,
            raw :class:`SearchResult` objects are returned.
    """

    storages: tuple[IndexedStorage, ...] = ()
    result_mapper: Callable[[SearchResult], R] | None = None

    @override
    def __call__(
        self,
        query: SearchQueryArg,
        max_results: SearchMaxResultsArg = 5,
        search_type: SearchTypeArg = "hybrid",
    ) -> list[R]:
        """Search indexed chunks using dense, sparse, or hybrid retrieval.

        Returns:
            List of results sorted by score descending.
        """
        all_results: list[SearchResult] = []

        for idx in self.storages:
            if not idx.storage.has_index():
                continue

            retriever = cbrkit.retrieval.dropout(
                cbrkit.retrieval.lancedb(
                    storage=idx.storage,
                    search_type=search_type,
                ),
                limit=max_results,
            )
            result = cbrkit.retrieval.apply_query_indexed(query, retriever)
            step = result.final_step.queries["default"]

            for key in step.ranking:
                str_key = cast(str, key)
                if idx.filter_func is not None and not idx.filter_func(str_key):
                    continue
                all_results.append(
                    SearchResult(
                        key=idx.prefixed(str_key),
                        text=step.casebase[key],
                        score=float(step.similarities[key]),
                    )
                )

        all_results.sort(key=lambda r: r.score, reverse=True)
        all_results = all_results[:max_results]

        if self.result_mapper is not None:
            return [self.result_mapper(r) for r in all_results]

        return cast(list[R], all_results)
