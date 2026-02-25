"""Retrieval evaluation tests using BM25 (sparse search, no embedding model needed)."""

import json
from pathlib import Path
from typing import Any

import pytest

from hivegent.chunks import chunk_document
from hivegent.retrieval import parse_chunk_key, search_sparse, sync_index
from hivegent.store import Casebase

pytestmark = pytest.mark.slow


def _seed_store(
    data_dir: Path,
    store: Casebase,
    annotations: list[dict[str, Any]],
) -> None:
    """Seed documents and chunks from annotations fixture."""
    docs_dir = store.documents_dir(data_dir)

    # Build document content from annotations
    doc_contents: dict[str, str] = {}
    for ann in annotations:
        for doc_name in ann["relevant_documents"]:
            if doc_name not in doc_contents:
                # Use the expected answer as document content
                doc_contents[doc_name] = (
                    f"# {doc_name}\n\n"
                    f"{ann['question']}\n\n"
                    f"{ann['expected_answer']}\n"
                )

    for doc_name, content in doc_contents.items():
        doc_path = docs_dir / doc_name
        doc_path.parent.mkdir(parents=True, exist_ok=True)
        doc_path.write_text(content, encoding="utf-8")
        chunk_document(store, doc_name, content)

    sync_index(store)


def test_sparse_search_finds_relevant_chunks(
    data_dir: Path,
    user_store: Casebase,
    annotations: list[dict[str, Any]],
) -> None:
    """BM25 search returns relevant chunks in top-k for each annotation."""
    _seed_store(data_dir, user_store, annotations)

    for ann in annotations:
        results = search_sparse(user_store, ann["question"], top_k=10)
        result_filenames = {parse_chunk_key(key)[0] for key, _, _ in results}

        for expected_doc in ann["relevant_documents"]:
            assert expected_doc in result_filenames, (
                f"Expected {expected_doc!r} in results for "
                f"query {ann['question']!r}, got {result_filenames}"
            )


def test_sparse_search_returns_nonempty(
    data_dir: Path,
    user_store: Casebase,
    annotations: list[dict[str, Any]],
) -> None:
    """Each annotation query returns at least one result."""
    _seed_store(data_dir, user_store, annotations)

    for ann in annotations:
        results = search_sparse(user_store, ann["question"], top_k=5)
        assert len(results) > 0, (
            f"No results for query {ann['question']!r}"
        )
