from typing import cast

from hivegent.dyn.commons.markdown import (
    get_first_float_name,
    get_float_names,
    is_caption,
)
from hivegent.dyn.util import upsert

from .model import Document, Float, Node, TextNode


def _match_section_captions_inplace(contentlist: list[Node]):
    """Match section captions to float nodes within a single section.

    Args:
        contentlist (list[Node]): The list of nodes to process

    Returns:
        None
    """
    idx = 0
    caption = ""
    caption_idx = -1
    float_idx = -1
    while idx < len(contentlist):
        node = contentlist[idx]
        if isinstance(node.data, TextNode):
            if not is_caption(node.data.content):
                caption_idx = -1
                float_idx = -1
                idx += 1
                continue
            if float_idx > -1:
                float = contentlist[float_idx]
                if not isinstance(float.data, Float):
                    raise ValueError("Float is not a float!")
                float.data.caption = node.data.content
                float.data.name = get_first_float_name(node.data.content)
                _ = contentlist.pop(idx)
                caption_idx = -1
                float_idx = -1
                continue
            else:
                caption = node.data.content
                caption_idx = idx

        # figure
        else:
            if node.data.caption:
                float_idx = -1
                caption_idx = -1
                idx += 1
                continue
            else:
                if caption_idx > -1:
                    node.data.caption = caption
                    node.data.name = get_first_float_name(caption)
                    _ = contentlist.pop(caption_idx)
                    caption_idx = -1
                    float_idx = -1
                    continue
                else:
                    float_idx = idx
        idx += 1


def match_captions_inplace(doc: Document):
    """Build float and float mention dictionaries within the Document.

    Args:
        doc (Document): The document to process

    Returns:
        None
    """
    tree = doc.tree
    for _, contentlist in tree:
        _match_section_captions_inplace(contentlist)

    floats: dict[str, Node] = {}
    for _, contentlist in tree:
        for c in contentlist:
            if isinstance(c.data, Float) and c.data.name:
                floats[c.data.name] = c
    doc.floats = floats

    floats_mentions: dict[str, list[Node]] = {}
    for _, contentlist in tree:
        for c in contentlist:
            if not isinstance(c.data, TextNode):
                continue
            for float_name in get_float_names(c.data.content):
                float_node = doc.get_float(float_name)
                if float_node is None:
                    continue
                mentioned = cast(Float, float_node.data).name
                # since doc.get_float may map a subfigure to its parent
                # we have to prevent duplicate entries in the parent's
                # float mentions
                if c in floats_mentions.get(mentioned, []):
                    continue
                upsert(c, mentioned, floats_mentions)
    doc.float_mentions = floats_mentions


def render_float(node: Float, short: bool = False) -> str:
    """short drops the caption and keeps only the summary: in papers a
    caption is regularly several times the length of the passage that
    references the float."""
    ret = f"[[{node.name}"
    if node.caption and not short:
        ret += f" | Caption: {node.caption}"
    if node.summary:
        ret += f" | Summary: {node.summary}"
    ret += "]]"
    return ret
