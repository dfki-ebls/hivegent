from pathlib import Path
from typing import Literal, cast

import jsonlines
import typer
from chonkie import Chunk, RecursiveChunker, SemanticChunker
from markitdown import MarkItDown, StreamInfo
from tqdm import tqdm

md = MarkItDown(
    enable_plugins=True,
)
app = typer.Typer()

TEXT_SUFFIXES = {".txt", ".md", ".json", ".jsonl"}


def convert(f: Path) -> str:
    """Reads a text file into a utf-8 formatted string. Needed to prevent MarkItDown to fall back to ascii.
    Args:
        f (Path): The file path to convert.

    Returns:
        str: The resulting text content of the file
    """
    if f.suffix.lower() in TEXT_SUFFIXES:
        with f.open("rb") as fh:
            return md.convert(
                # prevent markitdown reading text as ascii instead of utf-8
                fh,
                stream_info=StreamInfo(extension=f.suffix, charset="utf-8"),
            ).text_content
    return md.convert(f).text_content


@app.command()
def process_folder(
    infolder: Path,
    glob: str,
    maximum_chunk_size: int,
    outpath: Path,
    chunker: Literal["rec_char", "sem"],
):
    """
    Converts all documents within infolder into chunks. As output, a jsonl file with one line per chunk
    is generated. Each line contains a key (a numbered file reference) and the chunks text.
    """
    files = [f for f in infolder.glob(glob) if f.is_file()]
    if outpath.is_dir():
        outpath = outpath / f"{infolder.name}.jsonl"
    if outpath.suffix != ".jsonl":
        raise ValueError("Only JSONL Output is supported!")
    if chunker == "rec_char":
        chunking = RecursiveChunker(
            tokenizer="o200k_base", chunk_size=maximum_chunk_size
        )
    else:
        chunking = SemanticChunker(chunk_size=maximum_chunk_size)
    with jsonlines.open(outpath, "w") as writer:
        for f in tqdm(files):
            # serialization
            md_content = convert(f)
            # segmentation
            chunks = [cast(Chunk, c).text for c in chunking(md_content)]
            writer.write_all(
                {"key": f"{f}-{i}", "chunk": c} for i, c in enumerate(chunks)
            )


if __name__ == "__main__":
    app()
