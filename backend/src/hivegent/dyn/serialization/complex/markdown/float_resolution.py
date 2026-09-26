from collections.abc import Sequence
from typing import cast

from hivegent.dyn.commons.markdown import get_float_name_spans

from .model import Document, Float, Node, TextNode


def _resolve_sections_floats_inplace(contentlist: Sequence[Node], doc: Document):
    """Resolve float references within a section by replacing text segments with rendered float values.

    Args:
        contentlist (Sequence[Node): Sequence of content nodes
        doc (Document): The document object

    Returns:
        None: No value returned
    """
    for c in contentlist:
        if not isinstance(c.data, TextNode):
            continue
        text = c.data.content
        references = get_float_name_spans(text)
        if not references:
            continue
        for start, end, float_names in reversed(references):
            renders = [
                cast(Float, f.data).render()
                for f in (doc.get_float(n) for n in float_names)
                if f is not None
            ]
            if not renders:
                continue
            text = text[:start] + " and ".join(renders) + text[end:]
        c.data.content = text


def resolve_floats_inplace(doc: Document):
    """Resolve floats in all within the entire Document.

    Args:
        doc (Document): Document

    Returns:
        None: None
    """
    for _, contentlist in doc.tree:
        _resolve_sections_floats_inplace(contentlist, doc)
