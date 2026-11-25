"""LLM-guided chunking with chonkie, the app's since it calls the aux model."""

import asyncio
from dataclasses import dataclass
from typing import Annotated, Any

from chonkie import SlumberChunker
from chonkie.genie import BaseGenie
from exar.ingest import Chunk, annotate
from exar.ingest.chunkers import overlap
from pydantic import BaseModel, Field

from ..config import settings
from ..llm import complete
from ..llm_config import LlmConfig, aux_llm

__all__ = ["Slumber"]


class _LoopGenie(BaseGenie):
    """Genie that asks *config* through :func:`~hivegent.llm.complete` on *loop*, as every other aux call does.

    The chunker calls the genie from a worker thread, so each sync method hands
    its request back to *loop*.
    """

    def __init__(self, config: LlmConfig, loop: asyncio.AbstractEventLoop) -> None:
        super().__init__()
        self._config = config
        self._loop = loop

    async def agenerate(self, prompt: str) -> str:
        """Generate a plain text completion."""
        return await complete(
            [prompt],
            self._config,
            output_type=str,
            timeout=settings.llm.request_timeout_seconds,
        )

    async def agenerate_json(
        self, prompt: str, schema: type[BaseModel]
    ) -> dict[str, Any]:
        """Generate a completion parsed into *schema*."""
        parsed = await complete(
            [prompt],
            self._config,
            output_type=schema,
            timeout=settings.llm.request_timeout_seconds,
        )

        return parsed.model_dump()

    def generate(self, prompt: str) -> str:
        """Generate a plain text completion."""
        return asyncio.run_coroutine_threadsafe(
            self.agenerate(prompt), self._loop
        ).result()

    def generate_json(self, prompt: str, schema: type[BaseModel]) -> dict[str, Any]:
        """Generate a completion parsed into *schema*."""
        return asyncio.run_coroutine_threadsafe(
            self.agenerate_json(prompt, schema), self._loop
        ).result()


@dataclass(frozen=True, slots=True, kw_only=True)
class Slumber:
    """Chunks a document where the aux model decides a topic ends.

    The chunker is built per call, since it holds a live credential that a cache would pin.

    Args:
        size: Most tokens of a chunk.
        overlap: Characters of the next chunk appended to every chunk, which keeps its own span.
        candidate_size: Tokens of the window the model judges at once.
        min_characters: Fewest characters of a chunk.
    """

    size: Annotated[int, Field(ge=64, le=32768)] = 2048
    overlap: Annotated[int, Field(ge=0)] = 0
    candidate_size: Annotated[int, Field(ge=64, le=32768)] = 512
    min_characters: int = 24

    def _chunk(self, genie: BaseGenie, text: str) -> list[Chunk]:
        chunks = SlumberChunker(
            genie=genie,
            chunk_size=self.size,
            candidate_size=self.candidate_size,
            min_characters_per_chunk=self.min_characters,
        ).chunk(text)

        own = [
            Chunk(
                text=chunk.text,
                start=chunk.start_index,
                end=chunk.end_index,
                tokens=chunk.token_count,
            )
            for chunk in chunks
        ]

        return annotate(text, overlap(text, own, size=self.overlap))

    async def __call__(self, text: str, /) -> list[Chunk]:
        llm = aux_llm()

        if llm is None:
            raise ValueError("Slumber chunking needs the llm.aux model")

        genie = _LoopGenie(config=llm, loop=asyncio.get_running_loop())

        return await asyncio.to_thread(self._chunk, genie, text)
