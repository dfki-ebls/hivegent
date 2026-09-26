from functools import lru_cache

import spacy
from fastcoref import FCoref
from pydantic import BaseModel

from hivegent.dyn.commons.markdown import build_title
from hivegent.dyn.serialization.complex.markdown.model import (
    Document,
    FigureNode,
    TextNode,
)
from hivegent.dyn.util import get_token_count, truncate_to_tokens

from . import (
    fastcoref_compat as _fastcoref_compat,  # noqa: F401  (patches fastcoref for transformers>=5)
)
from .coref_merge import coref_merge_inplace
from .prechunk import Chunker
from .tables import split_tables_inplace
from sentence_transformers.util import get_device_name

_ELLIPSIS = "...\n\n"
_ELLIPSIS_TOKENS = get_token_count(_ELLIPSIS)


@lru_cache(maxsize=1)
def get_general_coref_model() -> FCoref:
    return FCoref(
        nlp=spacy.blank("en"),  # pyright: ignore[reportArgumentType]
        device=get_device_name(),
    )


@lru_cache(maxsize=1)
def get_math_coref_model(model_name: str) -> FCoref:
    return FCoref(
        model_name_or_path=model_name,
        nlp=spacy.blank("en"),  # pyright: ignore[reportArgumentType]
        device=get_device_name(),
    )


@lru_cache(maxsize=4)
def get_prechunker(limit: int) -> Chunker:
    return Chunker(limit)


def full_chunking(doc: Document, limit: int, math_coref_model: str):
    """Chunks `doc` in place.

    The coref models and the prechunker are cached process-wide: they are
    expensive to build and hold on to GPU/CPU memory, so rebuilding them per
    document exhausts memory when processing a whole folder.
    """
    prechunker = get_prechunker(limit)
    general_model = get_general_coref_model()
    math_model = get_math_coref_model(math_coref_model)
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


def _has_heading(text: str) -> bool:
    return text.startswith("# ")


def to_chunks(doc: Document, limit: int | None = None) -> list[Chunk]:
    ret: list[Chunk] = []
    for heading, contentlist in doc.tree:
        prefix = build_title(doc.title, heading) + "\n\n"
        for c in contentlist:
            if not isinstance(c.data, (TextNode, FigureNode)):
                raise TypeError(
                    f"Got {c.data}, but only TextNodes or FigureNodes are allowed in this step!"
                )

            c_text = c.data.content if isinstance(c.data, TextNode) else c.data.render()
            # truncate heading enrichment if it would exceed limit
            if not _has_heading(c_text) and limit is not None:
                heading = prefix
                if (
                    get_token_count(prefix) + (len_content := get_token_count(c_text))
                    > limit
                ):
                    budget = limit - len_content - _ELLIPSIS_TOKENS
                    heading = truncate_to_tokens(prefix, budget) + _ELLIPSIS
                c_text = heading + c_text

            ret.append(
                Chunk(
                    text=c_text,
                    lines=c.line_numbers,
                    start_idx=c.start_index,
                    end_idx=c.end_index,
                )
            )
    return ret
