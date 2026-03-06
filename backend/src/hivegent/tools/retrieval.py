"""Retrieval tools using cbrkit indexed backends."""

import logging
from collections.abc import Sequence
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
class SearchResult[K: (int, str)]:
    """A single search result with key, text, and relevance score."""

    key: K
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
class LanceDBSearchTool[K: (int, str)](Tool):
    """Search one or more LanceDB storages using cbrkit indexed retrieval.

    Builds a ``cbrkit.retrieval.lancedb`` retriever per storage, combines
    them with :func:`cbrkit.retrieval.combine`, and applies dropout
    limiting.

    Args:
        storages: One or more cbrkit LanceDB storage instances.
    """

    storages: Sequence[cbrkit.indexable.lancedb[K]]

    @override
    def __call__(
        self,
        query: SearchQueryArg,
        top_k: SearchTopKArg = 5,
        search_type: SearchTypeArg = "hybrid",
        where_clauses: Sequence[str | None] = (),
    ) -> list[SearchResult[K]]:
        """Search indexed chunks using dense, sparse, or hybrid retrieval.

        Returns:
            List of results sorted by score descending.
        """
        lancedb_retrievers: list[cbrkit.retrieval.lancedb[K]] = []

        for i, storage in enumerate(self.storages):
            if not storage.has_index():
                continue

            where = where_clauses[i] if i < len(where_clauses) else None
            lancedb_retrievers.append(
                cbrkit.retrieval.lancedb(
                    storage=storage,
                    search_type=search_type,
                    where=where,
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

        return [
            SearchResult(
                key=cast(K, key),
                text=step.casebase[key],
                score=float(step.similarities[key]),
            )
            for key in step.ranking
        ]
