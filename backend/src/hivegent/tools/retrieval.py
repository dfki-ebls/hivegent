"""Retrieval tool over the global cbrkit-backed vector index.

One storage backs every casebase; per-casebase scoping is enforced by
an optional ``filter_factory`` the caller supplies — its async return
is compiled to a SQL-level WHERE on the cbrkit query, so rows outside
the caller's scope never enter the candidate set.
"""

import logging
from collections.abc import Awaitable, Callable, Collection, Sequence
from dataclasses import dataclass
from typing import Annotated, Any, Literal, cast, override

import cbrkit
import logfire
from cbrkit import filter as cbrkit_filter
from pydantic import Field

from .base import AsyncTool, ToolOutput

__all__ = [
    "SearchMaxResultsArg",
    "SearchQueryArg",
    "SearchResult",
    "SearchType",
    "SearchTypeArg",
    "VectorSearchTool",
]

logger = logging.getLogger(__name__)

type SearchType = Literal["dense", "sparse", "hybrid"]

VectorStorage = cbrkit.typing.AsyncFilterableIndexableFunc[
    cbrkit.typing.Casebase[str, Any], Collection[str]
]


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
class VectorSearchTool[R = SearchResult](AsyncTool[list[R]]):
    """Search the global vector index with SQL-level scope filtering.

    Args:
        storage_factory: Async callable returning the cbrkit storage
            handle.  Resolved lazily so the tool can be constructed
            before storage is initialised.
        filter_factory: Async callable returning a cbrkit
            :class:`cbrkit_filter.Filter` (or ``None``) that scopes the
            search to the caller's accessible documents.  Resolved per
            call so the filter sees fresh data (e.g. newly accessible
            groups).
        result_mapper: Async callable that maps the raw ranked results
            to the final type — typically enriches with metadata from
            SQL.
    """

    storage_factory: Callable[[], Awaitable[VectorStorage]] | None = None
    filter_factory: Callable[[], Awaitable[cbrkit_filter.Filter | None]] | None = None
    result_mapper: Callable[[Sequence[SearchResult]], Awaitable[list[R]]] | None = None

    @override
    async def __call__(
        self,
        query: SearchQueryArg,
        max_results: SearchMaxResultsArg = 5,
        search_type: SearchTypeArg = "hybrid",
    ) -> ToolOutput[list[R]]:
        """Search indexed chunks using dense, sparse, or hybrid retrieval."""
        raw = await self._search(query, max_results, search_type)
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

    async def _search(
        self, query: str, max_results: int, search_type: SearchType
    ) -> list[SearchResult]:
        """Run the cbrkit query with the scope filter applied at SQL level."""
        if self.storage_factory is None:
            return []
        storage = await self.storage_factory()
        if not await storage.has_index():
            return []
        where = await self.filter_factory() if self.filter_factory is not None else None
        with logfire.span(
            "vector.search",
            search_type=search_type,
            max_results=max_results,
            query_length=len(query),
        ) as span:
            retriever = cbrkit.retrieval.pgvector_async(
                storage=cast(Any, storage),
                search_type=search_type,
                where=where,
                limit=max_results,
            )
            result = await cbrkit.retrieval.apply_query_indexed_async(query, retriever)
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
    lookup; either is a valid downstream of :class:`VectorSearchTool`.
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
