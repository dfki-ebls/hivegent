"""Retrieval evaluation tests using BM25 (sparse search, no embedding model needed)."""

from pathlib import Path
from typing import Any

import pytest

from hivegent.chunks import chunk_document
from hivegent.retrieval import build_search_tool, sync_index
from hivegent.store import Casebase

pytestmark = pytest.mark.slow


async def _seed_store(
    data_dir: Path,
    store: Casebase,
    annotations: list[dict[str, Any]],
) -> None:
    """Seed documents and chunks from annotations fixture."""
    docs_dir = store.workspace_dir(data_dir)

    # Build document content from annotations
    doc_contents: dict[str, str] = {}
    for ann in annotations:
        for doc_name in ann["relevant_documents"]:
            if doc_name not in doc_contents:
                # Use the expected answer as document content
                doc_contents[doc_name] = (
                    f"# {doc_name}\n\n{ann['question']}\n\n{ann['expected_answer']}\n"
                )

    for doc_name, content in doc_contents.items():
        doc_path = docs_dir / doc_name
        doc_path.parent.mkdir(parents=True, exist_ok=True)
        doc_path.write_text(content, encoding="utf-8")
        await chunk_document(store, doc_name, content)

    sync_index(store)


async def test_sparse_search_finds_relevant_chunks(
    data_dir: Path,
    user_store: Casebase,
    annotations: list[dict[str, Any]],
) -> None:
    """BM25 search returns relevant chunks in top-k for each annotation."""
    await _seed_store(data_dir, user_store, annotations)

    tool = build_search_tool([user_store])
    for ann in annotations:
        output = tool(ann["question"], search_type="sparse", max_results=10)
        result_filenames = {chunk.filename for chunk in output.data}

        for expected_doc in ann["relevant_documents"]:
            assert expected_doc in result_filenames, (
                f"Expected {expected_doc!r} in results for "
                f"query {ann['question']!r}, got {result_filenames}"
            )


async def test_sparse_search_returns_nonempty(
    data_dir: Path,
    user_store: Casebase,
    annotations: list[dict[str, Any]],
) -> None:
    """Each annotation query returns at least one result."""
    await _seed_store(data_dir, user_store, annotations)

    tool = build_search_tool([user_store])
    for ann in annotations:
        output = tool(ann["question"], search_type="sparse", max_results=5)
        assert len(output.data) > 0, f"No results for query {ann['question']!r}"
