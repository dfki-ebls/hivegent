import asyncio
from pathlib import Path

from hivegent.chunkers import ChunkingPipeline, get_chunker
from hivegent.converters import ConversionPipeline, get_converter


def test_dyn_converter() -> str:
    converter = get_converter(ConversionPipeline.DYN_WORD, filename="email.docx")
    result = asyncio.run(
        converter(
            Path(
                "~/Downloads/Firmenwagenregelung_Nordwerk_Maschinenbau.docx"
            ).expanduser()
        )
    )
    return result.markdown


def test_dyn_chunker() -> None:
    converted = test_dyn_converter()
    print(converted)
    print()
    chunker = get_chunker(ChunkingPipeline.DYN)
    chunks = asyncio.run(chunker(converted))

    assert chunks
    for chunk in chunks:
        print(f"--- chunk ({chunk.token_count} tokens) ---")
        print(chunk.text)
        print((chunk.start_line, chunk.end_line))
        print((chunk.start_index, chunk.end_index))


if __name__ == "__main__":
    # test_dyn_converter()
    test_dyn_chunker()
