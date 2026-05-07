"""Retrieval tools using cbrkit indexed backends."""

import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Annotated, Literal, cast, override

import cbrkit
from pydantic import Field

from .base import SyncTool, ToolOutput, apply_prefix

__all__ = [
    "IndexedStorage",
    "IndexedStorageFilterFunc",
    "LanceDBSearchTool",
    "SearchMaxResultsArg",
    "SearchQueryArg",
    "SearchResult",
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
class LanceDBSearchTool[R = SearchResult](SyncTool[list[R]]):
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
    ) -> ToolOutput[list[R]]:
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

        final: list[R]
        if self.result_mapper is not None:
            final = [self.result_mapper(r) for r in all_results]
        else:
            final = cast(list[R], all_results)

        if not final:
            return ToolOutput(data=final, formatted="(no results)")
        lines: list[str] = []
        for i, r in enumerate(final, 1):
            key = getattr(r, "key", None) or getattr(r, "filename", "?")
            chunk_idx = getattr(r, "chunk_index", None)
            score: float = getattr(r, "score", 0.0)
            text: str = getattr(r, "text", "")
            start_line = getattr(r, "start_line", None)
            end_line = getattr(r, "end_line", None)
            label = f"{key}#{chunk_idx}" if chunk_idx is not None else key
            if start_line is not None and end_line is not None:
                label += f" L{start_line}-{end_line}"
                text = _annotate_lines(text, start_line)
            lines.append(f"[{i}] {label} ({score:.0%})\n{text}")
        return ToolOutput(data=final, formatted="\n\n".join(lines))


def _annotate_lines(text: str, start_line: int) -> str:
    """Prefix each line of *text* with its 1-indexed line number.

    Without per-line annotations the LLM can only see the chunk's
    overall line range and has to guess which line a specific sentence
    is on, producing off-by-one citations.
    """
    return "\n".join(
        f"{start_line + i}: {line}" for i, line in enumerate(text.splitlines())
    )
