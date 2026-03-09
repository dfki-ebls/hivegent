"""Retrieval tools using cbrkit indexed backends."""

import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Annotated, Literal, cast, override

import cbrkit
from pydantic import Field

from .base import Tool

__all__ = [
    "LanceDBSearchTool",
    "SearchQueryArg",
    "SearchResult",
    "SearchTopKArg",
    "SearchType",
    "SearchTypeArg",
]

logger = logging.getLogger(__name__)

type SearchType = Literal["dense", "sparse", "hybrid"]


@dataclass(slots=True, frozen=True)
class SearchResult:
    """A single search result with key, text, and relevance score."""

    key: str
    text: str
    score: float


SearchQueryArg = Annotated[
    str,
    Field(description="Natural language search query."),
]
SearchTopKArg = Annotated[
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

    Builds a ``cbrkit.retrieval.lancedb`` retriever per storage, combines
    them with :func:`cbrkit.retrieval.combine`, and applies dropout
    limiting.

    Args:
        storages: One or more cbrkit LanceDB storage instances.
        key_filter: Optional predicate on result keys.  When ``None``,
            all keys are accepted.
        result_mapper: Optional callable that transforms each
            :class:`SearchResult` before it is returned.  When ``None``,
            raw :class:`SearchResult` objects are returned.
    """

    storages: Sequence[cbrkit.indexable.lancedb[str]]
    key_filter: Callable[[str], bool] | None = None
    result_mapper: Callable[[SearchResult], R] | None = None

    @override
    def __call__(
        self,
        query: SearchQueryArg,
        top_k: SearchTopKArg = 5,
        search_type: SearchTypeArg = "hybrid",
    ) -> list[R]:
        """Search indexed chunks using dense, sparse, or hybrid retrieval.

        Returns:
            List of results sorted by score descending.
        """
        lancedb_retrievers: list[cbrkit.retrieval.lancedb[str]] = []

        for storage in self.storages:
            if not storage.has_index():
                continue

            lancedb_retrievers.append(
                cbrkit.retrieval.lancedb(
                    storage=storage,
                    search_type=search_type,
                )
            )

        if not lancedb_retrievers:
            return []

        combined = cbrkit.retrieval.dropout(
            cbrkit.retrieval.combine(lancedb_retrievers),
            limit=top_k,
        )
        result = cbrkit.retrieval.apply_query_indexed(query, combined)
        step = result.final_step.queries["default"]

        results = [
            SearchResult(
                key=cast(str, key),
                text=step.casebase[key],
                score=float(step.similarities[key]),
            )
            for key in step.ranking
            if self.key_filter is None or self.key_filter(str(key))
        ]

        if self.result_mapper is not None:
            return [self.result_mapper(r) for r in results]

        return cast(list[R], results)
