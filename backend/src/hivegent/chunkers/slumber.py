"""LLM-guided document chunker using chonkie."""

import asyncio
from dataclasses import dataclass, field
from typing import Any

from chonkie import SlumberChunker
from chonkie.genie import BaseGenie
from chonkie.genie.openai import OpenAIGenie
from openai import AsyncOpenAI
from pydantic import BaseModel, Field

from .base import ChunkData, DocumentChunker
from .chonkie import BaseChonkieConfig, apply_chonkie

__all__ = ["SlumberChunkerConfig", "SlumberDocumentChunker"]


class SlumberChunkerConfig(BaseChonkieConfig):
    """Configuration for the Slumber chunking pipeline."""

    chunk_size: int = Field(
        default=2048,
        ge=64,
        le=32768,
        description="Target chunk size in tokens.",
    )
    candidate_size: int = Field(
        default=512,
        ge=64,
        description="Candidate window size for the LLM to evaluate.",
    )
    min_characters_per_chunk: int = Field(
        default=24,
        ge=1,
        description="Minimum character count for a chunk.",
    )


class _LoopGenie(OpenAIGenie):
    """``OpenAIGenie`` that runs its async calls on the loop owning *client*.

    The parent constructor is skipped since it builds its own HTTP clients,
    which would bypass the shared trusted client and ``network.unix_sockets``.
    The chunker calls the genie from a worker thread, so the sync methods hand
    the retried async requests back to *loop*.
    """

    def __init__(
        self, client: AsyncOpenAI, model: str, loop: asyncio.AbstractEventLoop
    ) -> None:
        BaseGenie.__init__(self)
        self.async_client = client
        self.model = model
        self._loop = loop

    def generate(self, prompt: str) -> str:
        """Generate a plain text completion."""
        return asyncio.run_coroutine_threadsafe(
            self.agenerate(prompt), self._loop
        ).result()

    def generate_json(self, prompt: str, schema: BaseModel) -> dict[str, Any]:
        """Generate a completion parsed into *schema*."""
        return asyncio.run_coroutine_threadsafe(
            self.agenerate_json(prompt, schema), self._loop
        ).result()


# SlumberChunker is intentionally not lru_cached: it holds a live LLM
# credential, so a process-global cache would pin rotated api_keys.
@dataclass(slots=True, frozen=True)
class SlumberDocumentChunker(DocumentChunker):
    """Chunker that uses an LLM to guide chunk boundary decisions.

    Uses chonkie's SlumberChunker with an OpenAI-compatible model.
    Best suited for documents needing intelligent, context-aware splitting.
    """

    name = "slumber"
    config: SlumberChunkerConfig = field(default_factory=SlumberChunkerConfig)

    def _chunk(self, genie: BaseGenie, text: str) -> list[ChunkData]:
        chunks = SlumberChunker(
            genie=genie,
            chunk_size=self.config.chunk_size,
            candidate_size=self.config.candidate_size,
            min_characters_per_chunk=self.config.min_characters_per_chunk,
        ).chunk(text)
        return apply_chonkie(chunks, self.config.refineries)

    async def _split(
        self,
        text: str,
        /,
        *,
        mime: str | None = None,
    ) -> list[ChunkData]:
        """Split text using LLM-guided chunking."""
        from ..llm import create_openai_client
        from ..llm_config import LlmConfig, resolve_llm_config

        llm = resolve_llm_config(LlmConfig())
        genie = _LoopGenie(
            client=create_openai_client(
                api_key=llm.api_key,
                base_url=llm.base_url,
                base_url_is_trusted=llm.base_url_is_trusted,
            ),
            model=llm.model,
            loop=asyncio.get_running_loop(),
        )
        return await asyncio.to_thread(self._chunk, genie, text)
