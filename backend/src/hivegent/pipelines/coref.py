"""Markdown chunks joined where the decision model finds one depends on the one before it."""

from dataclasses import dataclass
from typing import Annotated

from exar.ingest import Chunk, CorefMerge, MarkdownChunker, Offloaded
from pydantic import Field

from ..llm import judge_from_config
from ..llm_config import decision_llm

__all__ = ["MarkdownCoref"]


@dataclass(frozen=True, slots=True, kw_only=True)
class MarkdownCoref:
    """Chunks Markdown into small chunks of its sections, then joins those the ``llm.decision`` model finds dependent.

    The judge is built per call, since it holds a live credential that a fingerprint would pin.

    Args:
        size: Most tokens of a joined chunk.
        base: Most tokens of a chunk before joining, well below `size`, so there is room to join.
        context: Whether every chunk starts with the headings of its sections.
        threshold: Probability from which a chunk counts as dependent on the one before it.
    """

    size: Annotated[int, Field(ge=64, le=32768)] = 2048
    base: Annotated[int, Field(ge=64, le=32768)] = 512
    context: bool = False
    threshold: Annotated[float, Field(ge=0, le=1)] = 0.5

    async def __call__(self, text: str, /) -> list[Chunk]:
        llm = decision_llm()

        if llm is None:
            raise ValueError("Coreference chunking needs the llm.decision model")

        merge = CorefMerge(
            chunker=Offloaded(MarkdownChunker(size=self.base, context=self.context)),
            judge=judge_from_config(llm),
            size=self.size,
            threshold=self.threshold,
        )

        return await merge(text)
