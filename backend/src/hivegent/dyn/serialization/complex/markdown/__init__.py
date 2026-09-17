from pathlib import Path

from .float_captions import match_captions_inplace
from .float_resolution import resolve_floats_inplace
from .footnotes import inline_footnotes_inplace
from .hyphenation import fix_hypenation_inplace
from .info_extraction import summarize_all_floats
from .load import load
from .model import Document
from .premerge import premerge_inplace


def parse(input: Path | str) -> Document:
    """Parses an input file into a Document and applies the entire "Complex Document" processing pipeline
    (excluding segmentation).

    Args:
        input (Path | str): The input path or string

    Returns:
        Document: The processed document
    """
    doc = load(input)
    inline_footnotes_inplace(doc)
    match_captions_inplace(doc)
    resolve_floats_inplace(doc)
    # summarize_all_floats(doc)

    premerge_inplace(doc)
    fix_hypenation_inplace(doc)
    return doc
