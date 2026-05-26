"""Chonkie-specific utilities shared by all chonkie-based chunkers."""

from collections.abc import Sequence
from functools import lru_cache
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


@lru_cache(maxsize=4)
def _build_overlap_refinery(config_json: str) -> OverlapRefinery:
    cfg = ChonkieOverlapConfig.model_validate_json(config_json)
    return OverlapRefinery(
        context_size=cfg.context_size,
        mode=cfg.mode,
        method=cfg.method,
        merge=cfg.merge,
    )


def apply_chonkie(
    chunks: Sequence[Chunk],
    refineries: Sequence[ChonkieRefineryConfig],
) -> list[ChunkData]:
    """Convert chonkie Chunk objects to ChunkData and apply refineries.

    Args:
        chunks: Sequence of chonkie Chunk objects.
        refineries: Sequence of refinery configs to apply sequentially.

    Returns:
        List of refined ChunkData with sequential index values.
    """

    raw: Sequence[Chunk] = chunks
    for cfg in refineries:
        if isinstance(cfg, ChonkieOverlapConfig):
            raw = _build_overlap_refinery(cfg.model_dump_json()).refine(list(raw))
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
