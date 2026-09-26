import re
from collections.abc import Callable
from typing import NamedTuple, cast

import spacy
from spacy.tokenizer import Tokenizer  # pyright: ignore[reportUnknownVariableType]

_ABBREVIATIONS = [
    "fig.",
    "figs.",
    "eq.",
    "eqs.",
    "tab.",
    "tabs.",
    "sec.",
    "secs.",
    "ref.",
    "refs.",
    "no.",
    "al.",
    "cf.",
    "vs.",
    "ca.",
    "approx.",
    "resp.",
    "e.g.",
    "i.e.",
]


def build_sentencizer(max_length: int = 1_000_000_000) -> Callable[[str], list[str]]:
    """LLM-GENERATED. Returns a function splitting a text into its sentences.

    Backed by a minimal spaCy pipeline for sentence segmentation only.

    Uses the rule-based sentencizer instead of the parser, which avoids the
    parser/NER memory constraints behind spaCy's default 1M char limit and is
    orders of magnitude faster. Memory scales linearly without parser/NER, so
    the limit is effectively disabled (single text passages can exceed 20M
    chars, e.g. in concatenated software documentation).
    """
    nlp = spacy.blank("en")
    _ = nlp.add_pipe("sentencizer")
    # Language.tokenizer is annotated as a plain Callable[[str], Doc]; the
    # concrete object is a Tokenizer, which exposes add_special_case.
    tokenizer = cast(  # pyright: ignore[reportUnknownVariableType]
        Tokenizer, nlp.tokenizer
    )
    for abbr in _ABBREVIATIONS:
        for form in (abbr, abbr.capitalize(), abbr.upper()):
            tokenizer.add_special_case(  # pyright: ignore[reportUnknownMemberType]
                form, [{"ORTH": form}]
            )
    nlp.max_length = max_length

    def sentencize(text: str) -> list[str]:
        return [sent.text for sent in nlp(text).sents]

    return sentencize


# Roman numerals stay case-sensitive via (?-i:...) even though the patterns
# below are compiled IGNORECASE: lowercased, plenty of ordinary words are valid
# numerals ("mix", "dill", "civil") and would be read as float numbers.
_ROMAN = r"(?-i:(?=[MDCLXVI])M{0,3}(?:CM|CD|D?C{0,3})(?:XC|XL|L?X{0,3})(?:IX|IV|V?I{0,3}))(?!\w)"
_NUMBER = rf"(\d+(?:\.\d+)?|{_ROMAN})"

_KW_PATTERN = re.compile(
    rf"\b(fig(?:ure)?s?\.?|tables?\.?)\s+{_NUMBER}(\([a-zA-Z]\))?",
    re.IGNORECASE,
)
_FOLLOW_PATTERN = re.compile(
    rf"[ \t]*(?:and|,|&)[ \t]+{_NUMBER}(\([a-zA-Z]\))?",
    re.IGNORECASE,
)


class FloatNameSpan(NamedTuple):
    """LLM-GENERATED. One float reference in a text, with the names it resolves to.

    A single span may cover several numbers, e.g. 'Figs. 2 and 3' → one span,
    two names. Use the offsets when replacing text to avoid index drift.
    """

    start: int
    end: int
    names: list[str]


def get_float_name_spans(content: str) -> list[FloatNameSpan]:
    """LLM-GENERATED. Returns a FloatNameSpan for each float reference span."""
    result: list[FloatNameSpan] = []
    for match in _KW_PATTERN.finditer(content):
        keyword = match.group(1)
        prefix = "Figure" if re.match(r"fig", keyword, re.IGNORECASE) else "Table"

        def make_name(num: str, subfig: str = "") -> str:
            return f"{prefix} {num}{subfig}"

        names = [make_name(match.group(2), match.group(3) or "")]
        pos = match.end()
        while True:
            follow = _FOLLOW_PATTERN.match(content, pos)
            if not follow:
                break
            names.append(make_name(follow.group(1), follow.group(2) or ""))
            pos = follow.end()

        result.append(FloatNameSpan(match.start(), pos, names))

    return result


def get_float_names(content: str) -> list[str]:
    """Return the list of float names found in the content.

    Args:
        content (str): The input string

    Returns:
        list[str]: A list of float names
    """
    return [name for span in get_float_name_spans(content) for name in span.names]


def get_first_float_name(content: str) -> str:
    """Return the first float name found in the content.

    Args:
        content (str): The input string

    Returns:
        str: The first float name found or an empty string
    """
    float_names = get_float_names(content)
    if not float_names:
        return ""
    return float_names[0]


def is_caption(text: str) -> bool:
    """
    LLM-GENERATED.
    Returns True if the text starts with a standard Figure or Table label.
    Matches: 'Fig 1:', 'Fig. 1:', 'Figure 1.2.', 'Fig. 2(c):', 'Table 10 -',
    'TABLE I:', etc.
    """
    pattern = (
        rf"^(Fig(?:ure)?\.?|Table)\s+(?:\d+[\.\d]*|{_ROMAN})(\([a-zA-Z]\))?[\.:\s\-]"
    )

    # tolerate leading emphasis markers (e.g. "**Table 1:** ...")
    stripped = text.strip().lstrip("*_~ ")
    return bool(re.match(pattern, stripped, re.IGNORECASE))


def is_textpassage_incomplete(text: str) -> bool:
    """LLM-GENERATED. Determine if the input text passage is incomplete.

    Args:
        text (str): The text passage to check for incompleteness.

    Returns:
        bool: True if the passage is incomplete, False otherwise.
    """
    text = text.strip()
    SENTENCE_ENDINGS = re.compile(r'[.!?…]["\'»\)\]]*$')
    # if string starts with lower case chars or symbols, it is not considered complete
    if not text or not text[0].isupper():
        return True
    return not SENTENCE_ENDINGS.search(text)


_STRAY_TILDE_FENCE_RE = re.compile(r"(?m)^([ \t]{0,3})(~{3,})")


def escape_stray_tilde_fences(md_content: str) -> str:
    """LLM-GENERATED. Escapes the first tilde of any line starting with 3+ tildes (e.g. Python's
    traceback caret-highlight lines like `~~~~~~~~~~~~~~~~~~^^`), so markdown-it
    does not misparse them as a fenced-code-block delimiter.

    Without this, such a line opens a tilde-fenced code block; if no later line in
    the document happens to consist of an equal-or-longer run of tildes, the fence
    never closes and swallows everything up to EOF into a single unsplittable
    text node."""
    return _STRAY_TILDE_FENCE_RE.sub(r"\1\\\2", md_content)


_STRAY_HTML_BLOCK_OPENER_RE = re.compile(
    r"(?im)^([ \t]{0,3})(<(?:script|pre|style|textarea)\b|<\?)"
)


def escape_stray_html_block_openers(md_content: str) -> str:
    """LLM-GENERATED. Escapes the leading '<' of lines that would open a CommonMark HTML block
    of type 1 (`<script>`/`<pre>`/`<style>`/`<textarea>`) or type 3 (`<?...?>`),
    when these appear as plain-text placeholders rather than real markup, e.g.
    Python CLI docs use `<script>` as a metavariable, and PEP examples show Hack
    code starting with `<?hh`.

    Such an HTML block only closes on a matching end tag (or `?>`) appearing
    later in the document; if none ever appears, it swallows everything up to
    EOF into a single unsplittable text node, same failure mode as the tilde
    fence issue above."""
    return _STRAY_HTML_BLOCK_OPENER_RE.sub(
        lambda m: m.group(1) + "\\" + m.group(2), md_content
    )


def build_title(doc_title: str, heading: str) -> str:
    """Construct a markdown formatted title and heading.

    Args:
        doc_title (str): The document title
        heading (str): The section heading

    Returns:
        str: The formatted title and heading string
    """
    if not doc_title:
        if not heading:
            return ""
        return "# " + heading
    if not heading:
        return "# " + doc_title
    return f"# {doc_title}\n## {heading}\n"
