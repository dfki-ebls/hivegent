"""Ad-hoc script: convert a document with dyn and print the resulting chunks.

Usage:
    uv run python -m hivegent.test_converter [PATH] [--chunk-size N]
"""

import argparse
import asyncio
from pathlib import Path

from hivegent.chunkers.dyn import DynChunker, DynConfig
from hivegent.converters.dyn import DynMarkdownConverter

DEFAULT_PATH = Path(
    "/home/kilianb/masterarbeit/experiments/coref_data/test/"
    "232_GraspNet-1Billion_ A Large-Scale Benchmark for General Object Grasping/"
    "hybrid_auto/"
    "232_GraspNet-1Billion_ A Large-Scale Benchmark for General Object Grasping.md"
)


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", nargs="?", type=Path, default=DEFAULT_PATH)
    parser.add_argument("--chunk-size", type=int, default=2048)
    parser.add_argument(
        "--preview",
        type=int,
        default=300,
        help="Characters of each chunk to print (0 = full text).",
    )
    args = parser.parse_args()

    if not args.path.is_file():
        raise SystemExit(f"No such file: {args.path}")

    result = await DynMarkdownConverter()(args.path)
    markdown = result.markdown
    print(f"Converted {args.path.name}: {len(markdown)} chars")

    chunker = DynChunker(config=DynConfig(chunk_size=args.chunk_size))
    chunks = await chunker(markdown)
    print(f"Produced {len(chunks)} chunks\n")

    for chunk in chunks:
        header = (
            f"--- chunk {chunk.index} | {chunk.token_count} tokens "
            f"| chars {chunk.start_index}-{chunk.end_index} "
            f"| lines {chunk.start_line}-{chunk.end_line} ---"
        )
        print(header)
        text = chunk.text if args.preview <= 0 else chunk.text[: args.preview]
        print(text)
        if args.preview > 0 and len(chunk.text) > args.preview:
            print(f"... (+{len(chunk.text) - args.preview} chars)")
        print()

    total = sum(c.token_count for c in chunks)
    print(f"Total tokens: {total}")
    if chunks:
        print(f"Mean tokens/chunk: {total / len(chunks):.1f}")


if __name__ == "__main__":
    asyncio.run(main())
