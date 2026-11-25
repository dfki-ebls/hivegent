"""Retrieval tool over a memory of chunks, an `exar.typing.Memory`.

The caller supplies the memory, which scopes what a search may find, such as
the chunks of the documents the caller may read (`hivegent.retrieval`).
"""

from collections.abc import Awaitable, Callable, Mapping, Sequence
from typing import Annotated, Literal
from uuid import UUID

import logfire
from cbrkit.indexable import Dense, Hybrid, Search, Sparse
from exar.text import LineClip, hint_suffix, number_lines
from exar.tools import MAX_BATCH_ITEMS, effect
from exar.tools.batch import batch_field, tool_return
from exar.tools.common import FullLinesArg, line_clip
from exar.tools.files import BLOCK_SEP
from exar.typing import Hit, Memory
from pydantic import BaseModel, Field
from pydantic_ai import ToolReturn

from .base import ToolFunc

__all__ = [
    "SEARCHES",
    "RetrievedChunk",
    "SearchMaxResultsArg",
    "SearchQueriesArg",
    "SearchType",
    "SearchTypeArg",
    "search",
]

type SearchType = Literal["dense", "sparse", "hybrid"]

SEARCHES: Mapping[SearchType, Search] = {
    "dense": Dense(),
    "sparse": Sparse(),
    "hybrid": Hybrid(),
}
"""The cbrkit search of every search type."""


class RetrievedChunk(BaseModel):
    """A chunk retrieved from search, ranked by its position in the results.

    It carries no score: a hybrid score is normalised per query and several
    queries fuse their ranks, so a score orders the results but says nothing
    about how relevant any one is, and a threshold on it would mislead.
    """

    store_key: str | None = Field(
        default=None,
        description="Source store identifier (e.g. 'user:alice', 'group:eng')",
    )
    filename: str = Field(description="The document filename")
    chunk_index: int = Field(description="Chunk index within the document")
    text: str = Field(description="The chunk text content")
    token_count: int = Field(description="Number of tokens in the chunk")
    start_line: int = Field(
        description="1-based start line number in the original document"
    )
    end_line: int = Field(
        description="1-based end line number in the original document"
    )
    start_index: int = Field(
        description="Start character index in the original document (for frontend highlighting only)",
    )
    end_index: int = Field(
        description="End character index in the original document (for frontend highlighting only)",
    )
    image_path: str | None = Field(
        default=None,
        description="Workspace-relative path to the original image file when this chunk describes an image",
    )


SearchQueriesArg = Annotated[
    list[Annotated[str, Field(min_length=20)]],
    batch_field(
        "Concrete query phrases naming the task's subject, entities, and "
        "constraints rather than generic topics or padded wording, using only "
        "details known from the task or documents. For a short term, name, or "
        "code, use grep instead. Each query is searched on its own and the "
        "rankings merged, so a chunk several of them find ranks higher and is "
        "returned once. Phrase each in the language the documents are likely "
        "written in, and add phrasings in another language (e.g. English or "
        "the user's) or other wordings of the question in the same call "
        "rather than retrying.",
        max_items=MAX_BATCH_ITEMS,
    ),
]
_MAX_SEARCH_RESULTS = 1000
"""Ceiling on ``max_results``, which sizes the database query and its rerank pool."""

SearchMaxResultsArg = Annotated[
    int,
    Field(
        description="Maximum number of results to return.",
        ge=1,
        le=_MAX_SEARCH_RESULTS,
    ),
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


def search[V](
    *,
    memory: Callable[[SearchType], Awaitable[Memory[UUID, V]]],
    result_mapper: Callable[[Sequence[Hit[UUID, V]]], Awaitable[list[RetrievedChunk]]],
    max_line_chars: int = 2000,
) -> ToolFunc:
    """The tool that searches a memory of chunks with several queries at once.

    Args:
        memory: Async callable that returns the memory searched with a search
            type, such as the chunks of the documents the caller may read.
            Resolved per call, so the tool can be constructed before the
            index is initialised.
        result_mapper: Async callable that maps the hits to the chunks
            they are, typically enriching them with their documents from SQL.
        max_line_chars: Per-line truncation cap for the formatted output,
            guarding the context against a chunk carrying a base64-embedded
            image or other very long line, unless the call passes
            ``full_lines``.
    """

    @effect("read")
    async def search(
        queries: SearchQueriesArg,
        max_results: SearchMaxResultsArg = 10,
        search_type: SearchTypeArg = "hybrid",
        full_lines: FullLinesArg = False,
    ) -> ToolReturn[list[RetrievedChunk]]:
        """Search indexed chunks using dense, sparse, or hybrid retrieval.

        Start here to find content, since it scales to large collections,
        and use grep for short exact terms or structural patterns.
        Several queries are searched together and their rankings merged by
        reciprocal rank fusion into one list of at most ``max_results``.
        Results are candidates, not proof of relevance. Read and check the
        relevant passages against the task before using them in an answer.
        """
        with logfire.span(
            "vector.search",
            search_type=search_type,
            max_results=max_results,
            query_count=len(queries),
        ) as span:
            chunks = await memory(search_type)
            hits = await chunks.search(*dict.fromkeys(queries), limit=max_results)
            span.set_attribute("result_count", len(hits))

        final = await result_mapper(hits)

        if not final:
            return tool_return(final, "(no results)")

        clip = line_clip(max_line_chars, full_lines)

        return tool_return(
            final, _format_results(final, clip) + hint_suffix(clip.hints())
        )

    return search


def _format_results(results: Sequence[RetrievedChunk], clip: LineClip) -> str:
    """Render search results as a human/LLM-readable string block.

    *clip* truncates each line so a long line in a chunk cannot flood the
    context.
    """
    # The leading [i] is the relevance rank (results are sorted best-first).
    return BLOCK_SEP.join(
        f"[{i}] {r.filename}#{r.chunk_index} L{r.start_line}-{r.end_line}\n"
        + number_lines(r.text.splitlines(), r.start_line, clip)
        for i, r in enumerate(results, 1)
    )
