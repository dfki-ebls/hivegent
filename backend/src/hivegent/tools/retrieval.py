"""Retrieval tool using a cbrkit LanceDB backend.

One global storage; per-casebase scoping is enforced by an optional
key predicate supplied by the caller.  Asynchronous so the result
mapper can load chunk enrichment metadata from SQL on demand —
without preloading the entire casebase at tool-build time.
"""

import logging
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Annotated, Any, Literal, cast, override

import cbrkit
import logfire
from pydantic import Field

from .base import AsyncTool, ToolOutput

__all__ = [
    "LanceDBSearchTool",
    "SearchMaxResultsArg",
    "SearchQueryArg",
    "SearchResult",
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
class LanceDBSearchTool[R = SearchResult](AsyncTool[list[R]]):
    """Search a single LanceDB index with optional post-hoc filtering.

    Args:
        storage: The cbrkit LanceDB storage instance, or ``None`` for an
            empty result (matches the empty-index path).
        filter_func: Optional predicate on result keys.  Returns ``True``
            to keep the result.  Callers use it to enforce per-casebase
            access scoping against a globally shared index.
        result_mapper: Optional async callable that receives the filtered,
            ranked, truncated raw results and returns the final list.  The
            batch shape lets callers load enrichment metadata from SQL in
            one query instead of per-row.
    """

    storage: cbrkit.indexable.lancedb[str] | None = None
    filter_func: Callable[[str], bool] | None = None
    result_mapper: Callable[[Sequence[SearchResult]], Awaitable[list[R]]] | None = None

    @override
    async def __call__(
        self,
        query: SearchQueryArg,
        max_results: SearchMaxResultsArg = 5,
        search_type: SearchTypeArg = "hybrid",
    ) -> ToolOutput[list[R]]:
        """Search indexed chunks using dense, sparse, or hybrid retrieval."""
        raw = self._search(query, max_results, search_type)
        if self.filter_func is not None:
            raw = [r for r in raw if self.filter_func(r.key)]
        raw.sort(key=lambda r: r.score, reverse=True)
        raw = raw[:max_results]

        final: list[R] = (
            await self.result_mapper(raw)
            if self.result_mapper is not None
            else cast(list[R], raw)
        )

        if not final:
            return ToolOutput(data=final, formatted="(no results)")
        return ToolOutput(data=final, formatted=_format_results(final))

    def _search(
        self, query: str, max_results: int, search_type: SearchType
    ) -> list[SearchResult]:
        """Run the raw cbrkit query, returning unfiltered :class:`SearchResult`s."""
        if self.storage is None or not self.storage.has_index():
            return []
        with logfire.span(
            "lancedb.search",
            search_type=search_type,
            max_results=max_results,
            query_length=len(query),
        ) as span:
            retriever = cbrkit.retrieval.dropout(
                cbrkit.retrieval.lancedb(
                    storage=self.storage,
                    search_type=search_type,
                ),
                limit=max_results,
            )
            try:
                result = cbrkit.retrieval.apply_query_indexed(query, retriever)
            except RuntimeError as e:
                # LanceDB FTS raises an Arrow length-mismatch when a query
                # tokenizes to nothing (single chars, stopwords).
                if "lance error" not in str(e).lower():
                    raise
                logger.warning(
                    "LanceDB %s query failed for %r: %s", search_type, query, e
                )
                span.set_attribute("result_count", 0)
                return []
            step = result.final_step.queries["default"]
            results = [
                SearchResult(
                    key=cast(str, key),
                    text=step.casebase[key],
                    score=float(step.similarities[key]),
                )
                for key in step.ranking
            ]
            span.set_attribute("result_count", len(results))
            return results


def _format_results(results: Sequence[Any]) -> str:
    """Render search results as a human/LLM-readable string block.

    Accepts both :class:`SearchResult` and ``RetrievedChunk`` via attribute
    lookup; either is a valid downstream of :class:`LanceDBSearchTool`.
    """
    lines: list[str] = []
    for i, r in enumerate(results, 1):
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
    return "\n\n".join(lines)


def _annotate_lines(text: str, start_line: int) -> str:
    """Prefix each line of *text* with its 1-indexed line number.

    Without per-line annotations the LLM can only see the chunk's
    overall line range and has to guess which line a specific sentence
    is on, producing off-by-one citations.
    """
    return "\n".join(
        f"{start_line + i}: {line}" for i, line in enumerate(text.splitlines())
    )
