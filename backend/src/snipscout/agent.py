"""RAG agent with document retrieval tools."""

import re
from textwrap import dedent

from pydantic_ai import Agent, RunContext

from .documents import create_index, load_documents
from .documents import search_documents as bm25_search
from .types import (
    DocumentRange,
    DocumentStats,
    GrepMatch,
    RetrievedDocument,
)

__all__ = ["agent"]

# Load documents once at module level
_documents = load_documents()
_index, _filenames = create_index(_documents) if _documents else (None, [])


agent = Agent(
    system_prompt=dedent("""
    You are a helpful RAG (Retrieval-Augmented Generation) assistant.

    You have access to a collection of documents that you can search and retrieve.
    Use the available tools to find and read documents before answering questions.

    Be helpful, accurate, and cite which documents your information comes from.
  """).strip(),
)


@agent.tool
def get_document_count(ctx: RunContext[None]) -> int:
    """Get the total number of documents."""
    return len(_documents)


@agent.tool
def list_documents(ctx: RunContext[None]) -> list[str]:
    """List all available document filenames."""
    return list(_documents.keys())


@agent.tool
def get_document(ctx: RunContext[None], filename: str) -> str | None:
    """Get the full content of a specific document.

    Args:
        filename: The exact filename to retrieve.
    """
    return _documents.get(filename)


@agent.tool
def get_document_stats(ctx: RunContext[None], filename: str) -> DocumentStats | None:
    """Get statistics about a document.

    Args:
        filename: The document filename.
    """
    if filename in _documents:
        content = _documents[filename]
        lines = content.splitlines()
        return DocumentStats(
            line_count=len(lines),
            word_count=len(content.split()),
            char_count=len(content),
        )
    return None


@agent.tool
def get_document_range(
    ctx: RunContext[None],
    filename: str,
    start_line: int,
    end_line: int,
) -> DocumentRange | None:
    """Get a range of lines from a document.

    Args:
        filename: The document filename.
        start_line: First line to include (1-indexed).
        end_line: Last line to include (1-indexed).
    """
    if filename in _documents:
        lines = _documents[filename].splitlines()
        start = max(1, start_line) - 1
        end = min(len(lines), end_line)
        return DocumentRange(
            start_line=start + 1,
            end_line=end,
            content="\n".join(lines[start:end]),
        )
    return None


@agent.tool
def get_context(
    ctx: RunContext[None],
    filename: str,
    line: int,
    context: int = 3,
) -> DocumentRange | None:
    """Get lines surrounding a specific line.

    Args:
        filename: The document filename.
        line: The center line number (1-indexed).
        context: Number of lines before and after.
    """
    if filename in _documents:
        lines = _documents[filename].splitlines()
        center = max(1, min(line, len(lines)))
        start = max(1, center - context)
        end = min(len(lines), center + context)
        return DocumentRange(
            start_line=start,
            end_line=end,
            content="\n".join(lines[start - 1 : end]),
        )
    return None


@agent.tool
def grep_document(
    ctx: RunContext[None],
    filename: str,
    pattern: str,
) -> list[GrepMatch]:
    """Find lines matching a pattern in a specific document.

    Args:
        filename: The document filename.
        pattern: Regex pattern to search for (case-insensitive).
    """
    matches: list[GrepMatch] = []
    if filename not in _documents:
        return matches

    try:
        regex = re.compile(pattern, re.IGNORECASE)
    except re.error:
        return matches

    for i, line in enumerate(_documents[filename].splitlines(), start=1):
        if regex.search(line):
            matches.append(GrepMatch(filename=filename, line_number=i, line=line))

    return matches


@agent.tool
def grep_documents(
    ctx: RunContext[None],
    pattern: str,
    include_content: bool = False,
) -> list[GrepMatch]:
    """Find lines matching a pattern in document titles and content.

    Args:
        pattern: Regex pattern to search for (case-insensitive).
        include_content: Also search document content, not just filenames.
    """
    matches: list[GrepMatch] = []
    try:
        regex = re.compile(pattern, re.IGNORECASE)
    except re.error:
        return matches

    for filename, content in _documents.items():
        if regex.search(filename):
            matches.append(GrepMatch(filename=filename, line_number=0, line=filename))

        if include_content:
            for i, line in enumerate(content.splitlines(), start=1):
                if regex.search(line):
                    matches.append(GrepMatch(filename=filename, line_number=i, line=line))

    return matches


@agent.tool
async def search_documents(
    ctx: RunContext[None],
    query: str,
    top_k: int = 3,
) -> list[RetrievedDocument]:
    """Semantic search for documents using BM25 ranking.

    Args:
        query: Natural language search query.
        top_k: Maximum results to return.
    """
    if not _documents or not _index:
        return []

    results = bm25_search(query, _documents, _index, _filenames, top_k)

    return [
        RetrievedDocument(
            filename=filename,
            content=content,
            score=round(score, 4),
        )
        for filename, content, score in results
    ]
