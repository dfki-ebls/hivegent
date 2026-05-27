"""Chonkie-specific utilities shared by all chonkie-based chunkers."""

from collections.abc import Sequence
from typing import Annotated, Literal

from chonkie import OverlapRefinery
from chonkie.types.base import Chunk
from pydantic import BaseModel, Field

from .base import ChunkData

__all__ = [
    "BaseChonkieConfig",
    "ChonkieOverlapConfig",
    "ChonkieRefineryConfig",
    "apply_chonkie",
]


class ChonkieOverlapConfig(BaseModel):
    """Config for the chonkie OverlapRefinery."""

    type: Literal["overlap"] = "overlap"
    context_size: float = Field(default=0.25, ge=0)
    mode: Literal["token", "recursive"] = "token"
    method: Literal["suffix", "prefix"] = "suffix"
    merge: bool = True


# Tagged union — extend with `| NewRefineryConfig` when adding more refineries
ChonkieRefineryConfig = Annotated[ChonkieOverlapConfig, Field(discriminator="type")]


class BaseChonkieConfig(BaseModel):
    """Shared config for all chonkie-based chunkers."""

    refineries: list[ChonkieRefineryConfig] = Field(default_factory=list)


def apply_chonkie(
    chunks: Sequence[Chunk],
    refineries: Sequence[ChonkieRefineryConfig],
) -> list[ChunkData]:
    """Convert chonkie Chunk objects to ChunkData and apply refineries.

    Refineries are constructed fresh per call: they hold only their
    config and are not documented as safe to share across threads.
    """

    raw: Sequence[Chunk] = chunks
    for cfg in refineries:
        if isinstance(cfg, ChonkieOverlapConfig):
            raw = OverlapRefinery(
                context_size=cfg.context_size,
                mode=cfg.mode,
                method=cfg.method,
                merge=cfg.merge,
            ).refine(list(raw))
    return [
        ChunkData(
            text=c.text,
            token_count=c.token_count,
            start_index=c.start_index,
            end_index=c.end_index,
            index=i,
        )
        for i, c in enumerate(raw)
    ]
