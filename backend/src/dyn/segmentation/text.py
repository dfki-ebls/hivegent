from typing import cast, final

from chonkie import Chunk, SemanticChunker

from dyn.commons.markdown import build_sentencizer
from dyn.util import get_token_count

"""The chunker below facilitates chunking simple text documents such as emails.
To handle emails, it uses semantic segmentation to divide different topics into
different chunks, even if the structure does not indicate thematic shifts."""


@final
class Chunker:
    def __init__(self, limit: int):
        self.limit = limit
        self.sentencizer = build_sentencizer()
        self.sem = SemanticChunker(chunk_size=limit)

    def divide_text(self, text: str) -> list[str]:
        length = get_token_count(text)
        if length <= self.limit:
            return [text]
        sem_chunks = cast(list[Chunk], self.sem(text))
        return [c.text for c in sem_chunks]
