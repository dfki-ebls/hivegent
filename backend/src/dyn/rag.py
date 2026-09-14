from collections.abc import Callable, Iterable, Iterator, Sequence
from pathlib import Path
from typing import final

import cbrkit
import jsonlines
import numpy as np
import numpy.typing as npt
from pydantic_ai import Agent
from sentence_transformers import SentenceTransformer

from .config import DEFAULT_EMBEDDING_MODEL, DEFAULT_GENERATOR
from .util import format_retrieved

Embedder = Callable[[Sequence[str]], Sequence[npt.NDArray[np.float32]]]

DEFAULT_INDEX_BATCH_SIZE = 512
DEFAULT_ENCODE_BATCH_SIZE = 32

DEFAULT_MAX_SEQ_LENGTH = 2048
"""Input window of the embedding model, should match chunk size"""


def make_embedder(
    model_name: str | None = None,
    batch_size: int = DEFAULT_ENCODE_BATCH_SIZE,
    max_seq_length: int | None = DEFAULT_MAX_SEQ_LENGTH,
) -> Embedder:
    """Create an embedding function configured with specified model parameters.

    Args:
        model_name (str | None): The name of the model to use
        batch_size (int): The batch size for encoding
        max_seq_length (int | None): The maximum sequence length for encoding

    Returns:
        Embedder: An embedding function
    """
    # dtype="auto" honours the checkpoint's own precision (bf16 for the Qwen3
    # embedding models) instead of upcasting everything to fp32.
    model = SentenceTransformer(
        model_name or DEFAULT_EMBEDDING_MODEL, model_kwargs={"dtype": "auto"}
    )

    if max_seq_length is not None:
        model.max_seq_length = min(max_seq_length, model_max_seq_length(model))

    def embed(texts: Sequence[str], /) -> Sequence[npt.NDArray[np.float32]]:
        embeddings = model.encode(  # pyright: ignore[reportUnknownMemberType]
            list(texts),
            convert_to_numpy=True,
            batch_size=batch_size,
            normalize_embeddings=True,
        )
        return list(embeddings.astype(np.float32))

    return embed


def model_max_seq_length(model: SentenceTransformer) -> int:
    """LLM-GENERATED. Longest sequence the model's position embeddings can encode.

    Read off the underlying transformer config rather than
    `model.max_seq_length`, which is only the freely writable truncation length
    sentence-transformers happens to be configured with. Short-context models
    (all-MiniLM-L6-v2 stops at 512) otherwise fail in the forward pass with a
    shape mismatch between the token and the position tensor.
    """
    for module in model:
        config = getattr(getattr(module, "auto_model", None), "config", None)
        limit = getattr(config, "max_position_embeddings", None)

        if isinstance(limit, int) and limit > 0:
            return limit

    return model.max_seq_length


def open_storage(
    db_folder: Path,
    embedder: Embedder,
) -> cbrkit.indexable.lancedb[str, str]:
    """Open a LanceDB index using the provided folder and embedding function.

    Args:
        db_folder (Path): Path
        embedder (Embedder): Embedder

    Returns:
        cbrkit.indexable.lancedb[str, str]: The initialized LanceDB index
    """
    return cbrkit.indexable.lancedb[str, str](
        uri=str(db_folder),
        table_name=db_folder.name,
        index_type="dense",
        conversion_func=embedder,
    )


def read_chunks(files: Iterable[Path]) -> Iterator[tuple[str, str]]:
    """Yield key and chunk pairs from all JSON Lines files.

    Args:
        files (Iterable[Path]): The collection of files to read

    Returns:
        Iterator[tuple[str, str]]: An iterator of key-chunk string tuples
    """
    for file in files:
        with jsonlines.open(file, "r") as reader:
            for record in reader.iter(type=dict):
                yield str(record["key"]), str(record["chunk"])


def fill_storage(
    storage: cbrkit.indexable.lancedb[str, str],
    entries: Iterable[tuple[str, str]],
    batch_size: int = DEFAULT_INDEX_BATCH_SIZE,
    progress: Callable[[int], None] | None = None,
) -> int:
    """LLM-GENERATED. Embed and upsert entries into the storage in batches.

    Existing keys are overwritten, keys absent from `entries` are kept, so
    the function can be called repeatedly to extend a database.

    Args:
        storage (cbrkit.indexable.lancedb[str, str]): Target storage.
        entries (Iterable[tuple[str, str]]): Case key / text pairs.
        batch_size (int): Number of entries embedded and written at once.
        progress (Callable[[int], None] | None): Called with the batch size
            after every written batch.

    Returns:
        int: Number of entries written.
    """
    written = 0
    batch: dict[str, str] = {}

    def flush() -> None:
        nonlocal written
        if not batch:
            return
        storage.upsert_index(batch)
        written += len(batch)
        if progress is not None:
            progress(len(batch))
        batch.clear()

    for key, text in entries:
        batch[key] = text
        if len(batch) >= batch_size:
            flush()

    flush()
    return written


@final
class RAG:
    storage: cbrkit.indexable.lancedb[str, str]

    def __init__(
        self,
        db_folder: Path,
        embedding_model_name: str | None = None,
        generator_model_name: str | None = None,
    ):
        if not db_folder.exists():
            raise ValueError(
                "There is no vector database. If a new one should be created, you have to pass init_files!"
            )

        self.embed = make_embedder(embedding_model_name)
        self.storage = open_storage(db_folder, self.embed)

        self.retriever = cbrkit.retrieval.indexable.lancedb[str](
            storage=self.storage,
            limit=10,
            search_type="dense",
            normalize_scores=True,
        )
        self.agent = Agent(generator_model_name or DEFAULT_GENERATOR)

    def retrieve(self, query: str) -> dict[str, float]:
        """Retrieves 10 results for query. Returns a mapping document IDs to their similarity scores.

        Args:
            query (str): The search query.

        Returns:
            dict[str, float]: A mapping of document IDs to similarity scores
        """
        result = cbrkit.retrieval.apply_query_indexed(query, self.retriever)
        step = result.steps[-1].queries["default"]
        ranked = sorted(step.similarities.items(), key=lambda x: x[1], reverse=True)

        return {step.casebase[doc_id]: score for doc_id, score in ranked}

    async def rag(self, query: str) -> str:
        """Generate an answer using retrieved context and the provided query.

        Args:
            query (str): The search query

        Returns:
            str: The agent's output
        """
        docs = self.retrieve(query)
        context = format_retrieved(docs)
        prompt = f"Using the provided context, answer the following user query. Whenever you resort to the provided context, insert a citation with the exact text you base your answer upon in your answer. If you are unsure and the context does not provide enough information, answer with 'I don't know'.\nQUERY: {query}\n---\nCONTEXT: {context}"
        result = await self.agent.run([prompt])
        return result.output
