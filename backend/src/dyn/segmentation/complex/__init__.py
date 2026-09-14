from functools import lru_cache

import spacy
import torch
from fastcoref import FCoref
from pydantic import BaseModel, Field

from dyn.commons.markdown import build_title
from dyn.config import MATH_COREF_MODEL
from dyn.serialization.complex.markdown.model import (
    Document,
    FigureNode,
    Node,
    TextNode,
)
from dyn.util import get_token_count, truncate_to_tokens

from . import (
    fastcoref_compat as _fastcoref_compat,  # noqa: F401  (patches fastcoref for transformers>=5)
)
from .coref_merge import coref_merge_inplace
from .prechunk import Chunker
from .tables import split_tables_inplace

_ELLIPSIS = "...\n\n"
_ELLIPSIS_TOKENS = get_token_count(_ELLIPSIS)


@lru_cache(maxsize=1)
def get_device():
    if torch.cuda.is_available():
        # NVIDIA GPU
        return torch.device("cuda")
    elif torch.backends.mps.is_available():
        # Apple Silicon (M1/M2/M3/M4/M5)
        return torch.device("mps")
    else:
        # Fallback to CPU
        return torch.device("cpu")


@lru_cache(maxsize=1)
def get_general_coref_model() -> FCoref:
    return FCoref(device=get_device())


@lru_cache(maxsize=1)
def get_math_coref_model() -> FCoref:
    return FCoref(
        model_name_or_path=MATH_COREF_MODEL,
        nlp=spacy.blank("en"),  # pyright: ignore[reportArgumentType]
        device=get_device(),
    )


@lru_cache(maxsize=4)
def get_prechunker(limit: int) -> Chunker:
    return Chunker(limit)


def full_chunking(doc: Document, limit: int):
    """Chunks `doc` in place.

    The coref models and the prechunker are cached process-wide: they are
    expensive to build and hold on to GPU/CPU memory, so rebuilding them per
    document exhausts memory when processing a whole folder.
    """
    prechunker = get_prechunker(limit)
    general_model = get_general_coref_model()
    math_model = get_math_coref_model()
    prechunker.prechunk_inplace(doc, limit, limit)
    split_tables_inplace(doc, limit)
    coref_merge_inplace(doc, general_model, limit)
    coref_merge_inplace(doc, math_model, limit)

    # update start and end indices
    for _, contentlist in doc.tree:
        for node in contentlist:
            if not isinstance(node.data, TextNode):
                continue
            text = node.data.content
            original_text = doc.raw_text[node.start_index : node.end_index]
            if (offset := original_text.find(text)) != -1:
                start_idx = node.start_index + offset
                end_idx = start_idx + len(text)
                node.start_index = start_idx
                node.end_index = end_idx


class Chunk(BaseModel):
    text: str
    lines: tuple[int, int]
    start_idx: int
    end_idx: int


def to_chunks(doc: Document, limit: int | None = None) -> list[Chunk]:
    ret: list[Chunk] = []
    for heading, contentlist in doc.tree:
        prefix = build_title(doc.title, heading) + "\n\n"
        for c in contentlist:
            c_text = prefix
            if isinstance(c.data, FigureNode):
                c_text += c.data.render()
                ret.append(
                    Chunk(
                        text=c_text,
                        lines=c.line_numbers,
                        start_idx=c.start_idx,
                        end_idx=c.end_index,
                    )
                )
                continue
            if not isinstance(c.data, TextNode):
                raise TypeError(f"Got {c.data}, but only TextNodes allowed!")

            # truncate heading enrichment if it would exceed limit
            if (
                limit is not None
                and get_token_count(prefix)
                + (len_content := get_token_count(c.data.content))
                > limit
            ):
                budget = limit - len_content - _ELLIPSIS_TOKENS
                truncated = truncate_to_tokens(prefix, budget)
                c_text = truncated + _ELLIPSIS if truncated else ""

            c_text += c.data.content
            ret.append(
                Chunk(
                    text=c_text,
                    lines=c.line_numbers,
                    start_idx=c.start_index,
                    end_idx=c.end_index,
                )
            )
    return ret
