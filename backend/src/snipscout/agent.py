"""RAG agent with document retrieval tools."""

from dataclasses import dataclass
from fnmatch import fnmatch
from pathlib import Path

from pydantic_ai import Agent, FunctionToolset, RunContext
from ripgrepy import Ripgrepy

from .config import settings
from .documents import get_user_documents
from .documents import search_documents as bm25_search
from .types import (
    DocumentRange,
    DocumentSummary,
    GrepMatch,
    RetrievedDocument,
)

__all__ = ["UserDeps", "base_agent", "rag_toolset", "user_agent"]


@dataclass
class UserDeps:
    """Dependencies for user-specific agent operations."""

    user_id: str


base_agent: Agent[None, str] = Agent()
user_agent: Agent[UserDeps, str] = Agent(deps_type=UserDeps)

rag_toolset: FunctionToolset[UserDeps] = FunctionToolset()


@rag_toolset.tool
def list_documents(ctx: RunContext[UserDeps]) -> list[DocumentSummary]:
    """List all available documents with their sizes in bytes."""
    data_dir = settings.get_user_documents_dir(ctx.deps.user_id)
    if not data_dir.exists():
        return []
    return [
        DocumentSummary(filename=f.name, size=f.stat().st_size)
        for f in data_dir.iterdir()
        if f.is_file()
    ]


@rag_toolset.tool
def get_document(ctx: RunContext[UserDeps], filename: str) -> str | None:
    """Get the full content of a specific document.

    Args:
        filename: The exact filename to retrieve.
    """
    documents, _, _ = get_user_documents(ctx.deps.user_id)
    return documents.get(filename)


@rag_toolset.tool
def get_document_lines(
    ctx: RunContext[UserDeps],
    filename: str,
    start: int = 1,
    end: int | None = None,
) -> DocumentRange | None:
    """Get a range of lines from a document.

    Args:
        filename: The document filename.
        start: First line to include (1-indexed, default: 1).
        end: Last line to include (1-indexed, default: end of file).
    """
    documents, _, _ = get_user_documents(ctx.deps.user_id)
    if filename not in documents:
        return None

    lines = documents[filename].splitlines()
    total = len(lines)
    start = max(1, start)
    end = min(total, end) if end else total

    return DocumentRange(
        start_line=start,
        end_line=end,
        total_lines=total,
        content="\n".join(lines[start - 1 : end]),
    )


@rag_toolset.tool
def glob_documents(
    ctx: RunContext[UserDeps],
    pattern: str,
) -> list[str]:
    """Find documents matching a glob pattern.

    Args:
        pattern: Glob pattern to match (e.g., "*.md", "notes/*.txt", "**/*.py").
    """
    documents, _, _ = get_user_documents(ctx.deps.user_id)
    return [name for name in documents.keys() if fnmatch(name, pattern)]


@rag_toolset.tool
def grep(
    ctx: RunContext[UserDeps],
    pattern: str,
    glob: str | None = None,
    context_lines: int = 0,
    include_content: bool = True,
) -> list[GrepMatch]:
    """Search documents for a pattern.

    Uses smart case matching: case-insensitive unless the pattern contains
    uppercase letters.

    Args:
        pattern: Text or regex pattern to search for.
        glob: Only search files matching this pattern (e.g., "*.md", "notes/*").
        context_lines: Number of lines to show before and after each match.
        include_content: Whether to include the matching line content.
    """
    data_dir = settings.get_user_documents_dir(ctx.deps.user_id)
    if not data_dir.exists():
        return []

    rg = Ripgrepy(pattern, str(data_dir)).smart_case()

    if glob:
        rg = rg.glob(glob)
    if context_lines > 0:
        rg = rg.context(context_lines)

    matches: list[GrepMatch] = []
    try:
        for item in rg.run().as_dict:
            if item.get("type") == "match":
                data = item["data"]
                filepath = data["path"]["text"]
                doc_name = str(Path(filepath).relative_to(data_dir))
                content = (
                    data["lines"]["text"].rstrip("\n") if include_content else None
                )
                matches.append(
                    GrepMatch(
                        filename=doc_name,
                        line=data["line_number"],
                        content=content,
                    )
                )
    except Exception:
        pass

    return matches


@rag_toolset.tool
async def search_documents(
    ctx: RunContext[UserDeps],
    query: str,
    top_k: int = 3,
) -> list[RetrievedDocument]:
    """Semantic search for documents using BM25 ranking.

    Args:
        query: Natural language search query.
        top_k: Maximum results to return.
    """
    documents, index, filenames = get_user_documents(ctx.deps.user_id)
    if not documents or not index:
        return []

    results = bm25_search(query, documents, index, filenames, top_k)

    return [
        RetrievedDocument(
            filename=filename,
            content=content,
            score=round(score, 4),
        )
        for filename, content, score in results
    ]
