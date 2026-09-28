import re

from .model import Document, TextNode

# Hyphenation introduced by the PDF extraction: a word fragment, a hyphen, then
# either a line break or spaces before the rest of the word. The surrounding
# letter assertions keep hyphens that are flanked by non-letters (LaTeX minus
# signs, "1 - M") from matching.
_HYPHEN = r"(?<=[A-Za-z])-(?:[ \t]*\n[ \t]*|[ \t]+)(?=[A-Za-z])"

# Math spans are matched only so they can be handed back unchanged; $$...$$ is
# tried first so a display delimiter is never read as two inline ones. Inline
# math may not span lines, which bounds the damage from an unpaired '$'.
_MATH = r"\$\$.*?\$\$|\$[^$\n]*\$"

_MATH_OR_HYPHEN = re.compile(rf"(?P<math>{_MATH})|{_HYPHEN}", re.DOTALL)


def remove_hyphenation(text: str) -> str:
    """LLM-GENERATED. Join words split by hyphenation, leaving math spans untouched.

    "word-\\n  end" and "word-   end" both become "wordend". Anything inside
    $...$ or $$...$$ is returned verbatim, so LaTeX minus signs survive.
    """
    return _MATH_OR_HYPHEN.sub(lambda m: m.group("math") or "", text)


def fix_hypenation_inplace(doc: Document):
    """Remove hyphenation in the document content.

    Args:
        doc (Document): The document to process.

    Returns:
        None: None
    """
    tree = doc.tree
    for _, contentlist in tree:
        for node in contentlist:
            if isinstance(node.data, TextNode):
                node.data.content = remove_hyphenation(node.data.content)
