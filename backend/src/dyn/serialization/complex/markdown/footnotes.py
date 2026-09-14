import re
from collections.abc import Mapping

from .model import Document, TextNode

FOOTNOTE_REF_RE = re.compile(r"(?<!\\)\[\^([^\[\]\s^]+)\]")


def _replace_footnote_refs(
    markdown: str,
    footnotes: Mapping[str, str],
) -> str:
    """LLM-GENERATED. Replace each ``[^label]`` reference with ``footnotes[label]``.

    Args:
        markdown: The source text.
        footnotes: Maps footnote labels (without the ``[^`` and ``]``) to
            their replacement text.
        default: What to use when a label is missing from ``footnotes``.
            ``None`` leaves the reference untouched; a string is used as-is;
            a callable is passed the label and must return a string.
        skip_definitions: Leave ``[^label]: ...`` definition lines alone.

    Returns:
        The text with every matched reference replaced.
    """

    def substitute(match: re.Match[str]) -> str:
        label = match.group(1)
        if label not in footnotes:
            # Not a footnote reference, e.g. a regex character class like [^.]
            return match.group(0)
        return f" ({footnotes[label]}) "

    return FOOTNOTE_REF_RE.sub(substitute, markdown)


def inline_footnotes_inplace(doc: Document):
    """Inlines footnotes within the document content.

    Args:
        doc (Document): The document to process

    Returns:
        None
    """
    tree, footnote_mentions = doc.tree, doc.mentions
    for _, contentlist in tree:
        for content in contentlist:
            if isinstance(content.data, TextNode):
                content.data.content = _replace_footnote_refs(
                    content.data.content, footnote_mentions
                )
