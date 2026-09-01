"""Retrieval tool over the global cbrkit-backed vector index.

One storage backs every casebase; per-casebase scoping is enforced by
an optional ``filter_factory`` the caller supplies — its async return
is compiled to a SQL-level WHERE on the cbrkit query, so rows outside
the caller's scope never enter the candidate set.
"""

import logging
from collections.abc import Awaitable, Callable, Collection, Sequence
from dataclasses import dataclass
from typing import Annotated, Any, ClassVar, Literal, cast, override

import cbrkit
import logfire
from cbrkit import filter as cbrkit_filter
from cbrkit.typing import AsyncRetrieverFunc
from pydantic import Field

from .base import ToolOutput
from .formatting import BLOCK_SEP, annotate_lines, cap_lines, truncate_block
from .sink import OutputPathArg, RedirectedOutput, RedirectingTool

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
class VectorSearchTool[R = SearchResult](RedirectingTool[list[R]]):
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
        reranker_factory: Async callable returning a cbrkit reranker (or
            ``None`` when reranking is disabled).  When present, the base
            retriever over-fetches and the reranker rescores the candidate
            pool as a second pipeline stage.
        candidate_multiplier: How many times ``max_results`` to fetch as the
            rerank candidate pool.  Only applied when a reranker is active.
        max_line_chars: Per-line truncation cap for the formatted output,
            guarding the context against a chunk carrying a base64-embedded
            image or other very long line.
        max_formatted_chars: Whole-output budget for the formatted results,
            dropping trailing results the per-line cap cannot bound: chunk
            size is a chunker setting, so N results have no ceiling here.
    """

    injectable: ClassVar[bool] = True
    """Retrieval reaches the database, which no program can do for itself."""

    storage_factory: Callable[[], Awaitable[VectorStorage]] | None = None
    filter_factory: Callable[[], Awaitable[cbrkit_filter.Filter | None]] | None = None
    result_mapper: Callable[[Sequence[SearchResult]], Awaitable[list[R]]] | None = None
    reranker_factory: (
        Callable[[], Awaitable[AsyncRetrieverFunc[str, str, float] | None]] | None
    ) = None
    candidate_multiplier: int = 1
    max_line_chars: int = 2000
    max_formatted_chars: int = 50_000

    @override
    async def __call__(
        self,
        query: SearchQueryArg,
        max_results: SearchMaxResultsArg = 10,
        search_type: SearchTypeArg = "hybrid",
        output_path: OutputPathArg = None,
    ) -> ToolOutput[list[R] | RedirectedOutput]:
        """Search indexed chunks using dense, sparse, or hybrid retrieval."""
        raw = await self._search(query, max_results, search_type)
        raw.sort(key=lambda r: r.score, reverse=True)
        raw = raw[:max_results]

        final: list[R] = (
            await self.result_mapper(raw)
            if self.result_mapper is not None
            else cast(list[R], raw)
        )

        formatted = (
            _format_results(final, self.max_line_chars, self.max_formatted_chars)
            if final
            else "(no results)"
        )

        return await self.redirect(
            ToolOutput(data=final, formatted=formatted), output_path
        )

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
        reranker = (
            await self.reranker_factory() if self.reranker_factory is not None else None
        )
        candidate_pool = (
            max_results * self.candidate_multiplier
            if reranker is not None
            else max_results
        )
        with logfire.span(
            "vector.search",
            search_type=search_type,
            max_results=max_results,
            query_length=len(query),
            reranked=reranker is not None,
            candidate_pool=candidate_pool,
        ) as span:
            retriever = cbrkit.retrieval.indexable.pgvector_async(
                storage=cast(Any, storage),
                search_type=search_type,
                where=where,
                limit=candidate_pool,
            )
            retrievers: list[Any] = (
                [retriever, reranker] if reranker is not None else [retriever]
            )
            result = await cbrkit.retrieval.apply_query_indexed_async(query, retrievers)
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


def _format_results(
    results: Sequence[Any], max_line_chars: int, max_formatted_chars: int | None = None
) -> str:
    """Render search results as a human/LLM-readable string block.

    Accepts both :class:`SearchResult` and ``RetrievedChunk`` via attribute
    lookup; either is a valid downstream of :class:`VectorSearchTool`.
    *max_line_chars* truncates each line so a long line in a chunk cannot
    flood the context, and *max_formatted_chars* drops trailing results once
    the rendered block exceeds the budget.
    """
    blocks: list[str] = []
    for i, r in enumerate(results, 1):
        key = getattr(r, "key", None) or getattr(r, "filename", "?")
        chunk_idx = getattr(r, "chunk_index", None)
        text: str = getattr(r, "text", "")
        start_line = getattr(r, "start_line", None)
        end_line = getattr(r, "end_line", None)
        label = f"{key}#{chunk_idx}" if chunk_idx is not None else key
        if start_line is not None and end_line is not None:
            label += f" L{start_line}-{end_line}"
            text = annotate_lines(text.splitlines(), start_line, max_line_chars)
        else:
            text = truncate_block(text, max_line_chars)
        # The leading [i] is the relevance rank (results are sorted best-first).
        # We deliberately omit the score: cbrkit min-max normalizes per query,
        # so it pins the top hit to 100% and the worst to 0% even when every
        # result is off-topic, which misleads the model. Rank carries the
        # ordering without fabricating an absolute relevance magnitude.
        blocks.append(f"[{i}] {label}\n{text}")

    body, omitted = cap_lines(blocks, max_formatted_chars, BLOCK_SEP)
    if not omitted:
        return body
    return (
        f"{body}{BLOCK_SEP}({omitted} of {len(blocks)} results omitted, "
        "narrow the query or lower max_results)"
    )
