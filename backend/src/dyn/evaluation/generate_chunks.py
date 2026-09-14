from pathlib import Path

import jsonlines
import typer
from tqdm import tqdm

from dyn.pipelines.auto import to_chunks

app = typer.Typer()


@app.command()
def process_folder(infolder: Path, glob: str, maximum_chunk_size: int, outpath: Path):
    """
    Converts all documents within infolder into chunks. As output, a jsonl file with one line per chunk
    is generated. Each line contains a key (a numbered file reference) and the chunks text.
    """
    files = [f for f in infolder.glob(glob) if f.is_file()]
    if outpath.is_dir():
        outpath = outpath / f"{infolder.name}.jsonl"
    if outpath.suffix != ".jsonl":
        raise ValueError("Only JSONL Output is supported!")
    # write incrementally so the chunks of all documents are never held at once
    with jsonlines.open(outpath, "w") as writer:
        for f in tqdm(files):
            writer.write_all(
                {"key": f"{f}-{i}", "chunk": c}
                for i, c in enumerate(to_chunks(f, maximum_chunk_size))
            )


if __name__ == "__main__":
    app()
