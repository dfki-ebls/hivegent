import math
import os

os.environ.setdefault("PYTORCH_CUDA_ALLOC_CONF", "expandable_segments:True")

from collections.abc import Iterable, Iterator  # noqa: E402
from itertools import islice  # noqa: E402
from pathlib import Path  # noqa: E402

import lancedb  # noqa: E402
import numpy as np  # noqa: E402
import pyarrow as pa  # noqa: E402
import typer  # noqa: E402
from lancedb.index import BTree, IvfHnswSq  # noqa: E402
from tqdm import tqdm  # noqa: E402

from dyn.rag import (  # noqa: E402
    DEFAULT_ENCODE_BATCH_SIZE,
    DEFAULT_INDEX_BATCH_SIZE,
    DEFAULT_MAX_SEQ_LENGTH,
    Embedder,
    make_embedder,
    read_chunks,
)

app = typer.Typer()

KEY_COLUMN = "key"
VALUE_COLUMN = "value"
VECTOR_COLUMN = "vector"


def count_lines(file: Path) -> int:
    with file.open("r") as handle:
        return len(handle.readlines())


def batched[T](entries: Iterable[T], size: int) -> Iterator[list[T]]:
    """LLM-GENERATED. Yield batches of elements from an iterable based on a specified size.

    Args:
        entries (Iterable[T]): Input iterable
        size (int): Desired batch size

    Returns:
        Iterator[list[T]]: An iterator yielding lists of elements
    """
    iterator = iter(entries)

    while batch := list(islice(iterator, size)):
        yield batch


def write_table(
    db_folder: Path,
    entries: Iterable[tuple[str, str]],
    embed: Embedder,
    batch_size: int = DEFAULT_INDEX_BATCH_SIZE,
    progress: object = None,
) -> int:
    """LLM-GENERATED. Write text entries to a database table with vector indexing.

    Args:
        db_folder (Path): The folder path for the database.
        entries (Iterable[tuple[str, str]]): Iterable of text-pair entries.
        embed (Embedder): The object used for embedding text.
        batch_size (int): The batch size for processing entries.
        progress (object): An object to report progress during writing.

    Returns:
        int: The total number of entries written.
    """

    db = lancedb.connect(str(db_folder))
    table = None
    written = 0

    for batch in batched(entries, batch_size):
        vectors = [
            np.asarray(vector, dtype=np.float32)
            for vector in embed([text for _, text in batch])
        ]

        if table is None:
            schema = pa.schema(
                [
                    (KEY_COLUMN, pa.string()),
                    (VALUE_COLUMN, pa.string()),
                    (VECTOR_COLUMN, pa.list_(pa.float32(), len(vectors[0]))),
                ]
            )
            table = db.create_table(db_folder.name, schema=schema, mode="overwrite")

        table.add(
            pa.table(
                {
                    KEY_COLUMN: pa.array([key for key, _ in batch], pa.string()),
                    VALUE_COLUMN: pa.array([text for _, text in batch], pa.string()),
                    VECTOR_COLUMN: pa.FixedSizeListArray.from_arrays(
                        pa.array(np.concatenate(vectors), pa.float32()), len(vectors[0])
                    ),
                },
                schema=table.schema,
            )
        )
        written += len(batch)

        if progress is not None:
            progress(len(batch))  # pyright: ignore[reportCallIssue]

    if table is None:
        return 0

    # The scalar index is what cbrkit.indexable.lancedb builds itself; the
    # vector index is what makes retrieval an ANN search instead of a scan.
    table.create_index(KEY_COLUMN, config=BTree(), replace=True)
    rows = table.count_rows()
    table.create_index(
        VECTOR_COLUMN,
        config=IvfHnswSq(
            distance_type="cosine", num_partitions=max(1, min(256, math.isqrt(rows)))
        ),
        replace=True,
    )

    return written


@app.command()
def build(
    infolder: Path,
    db_folder: Path,
    glob: str = "*.jsonl",
    embedding_model: str | None = None,
    batch_size: int = DEFAULT_INDEX_BATCH_SIZE,
    encode_batch_size: int = DEFAULT_ENCODE_BATCH_SIZE,
    max_seq_length: int = DEFAULT_MAX_SEQ_LENGTH,
):
    """Build a vector database from the files in infolder.glob(glob) and saves it at db_folder."""
    files = sorted(infolder.glob(glob))
    if not files:
        raise ValueError(f"No files matching {glob!r} in {infolder}")

    db_folder.mkdir(parents=True, exist_ok=True)
    embed = make_embedder(embedding_model, encode_batch_size, max_seq_length)

    total = sum(count_lines(file) for file in files)

    with tqdm(total=total, unit="chunk") as bar:
        written = write_table(
            db_folder,
            read_chunks(files),
            embed,
            batch_size=batch_size,
            progress=bar.update,
        )

    typer.echo(f"Indexed {written} chunks into {db_folder}/{db_folder.name}")


if __name__ == "__main__":
    app()
