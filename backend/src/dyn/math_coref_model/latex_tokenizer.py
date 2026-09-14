"""
LLM-GENERATED.
spaCy tokenizer specialized for text containing LaTeX math expressions.

Tokenization rules (in priority order):
  1. \\word  – LaTeX commands are always a single token.
  2. [A-Z]+  – runs of uppercase letters (SARS, COVID, …).
  3. [a-z]+  – runs of lowercase letters (individuals, mice, …).
  4. [0-9]+  – runs of digits.
  5. [^\\s]  – any other non-whitespace character is its own token.
     This includes $, {, }, %, (, ), punctuation, …

Splitting alphanumeric runs by character class prevents common mid-token
failures such as citation superscripts ("individuals5" → "individuals"+"5"),
formula subscripts ("H2l" → "H"+"2"+"l"), and mixed-case identifiers.
$ is a plain single-char token so spans that cover a full $…$ expression
still have valid boundaries, while spans that reference content *inside*
math mode also align correctly.

Known limitation: 2 spans in test_fixed.jsonl reference the singular stem
of a word that appears in plural form in the text (e.g. "variant" inside
"variants").  These are annotation errors; no general tokenizer can split
inflectional suffixes.
"""

import json
import re
from typing import NotRequired, TypedDict, cast

import spacy
from spacy.language import Language
from spacy.tokens import Doc
from spacy.vocab import Vocab

# ---------------------------------------------------------------------------
# Token regex – order of alternatives matters
# ---------------------------------------------------------------------------
# Design rationale:
#
#   • LaTeX commands (\word) are a single token.
#   • We do NOT make $…$ a single token: spans often reference content
#     *inside* math mode, so $ is just a single-char punctuation token.
#   • Alphanumeric runs are split by character class to avoid merging:
#       – citation superscripts:   "individuals5"  → "individuals" + "5"
#       – formula subscripts:      "H2l"           → "H" + "2" + "l"
#       – mixed-case identifiers:  "Val70Phe"      → "V" + "al" + "70" + "P" + "he"
#     Rules:
#       [A-Z]+  – run of uppercase letters (SARS, COVID)
#       [a-z]+  – run of lowercase letters (individuals, mice)
#       [0-9]+  – run of digits
#   • Everything else is a single-char token (punctuation, $, {, }, …).
_LATEX_CMD = r"\\[a-zA-Z@]+"  # \command (must come first)
_UPPER = r"[A-Z]+"  # uppercase run
_LOWER = r"[a-z]+"  # lowercase run
_DIGITS = r"[0-9]+"  # digit run
_OTHER = r"[^\s]"  # any other single non-whitespace char

TOKEN_RE = re.compile(
    f"(?:{_LATEX_CMD}|{_UPPER}|{_LOWER}|{_DIGITS}|{_OTHER})",
    re.DOTALL,
)


def find_token_spans(text: str) -> list[tuple[int, int]]:
    """Return (start, end) char spans for every token in *text*.

    Whitespace between tokens is returned as its own span when it is more
    than a single space (e.g. newlines, double spaces).  Single spaces are
    NOT returned as separate spans; instead they are encoded as the
    ``spaces`` flag on the preceding token in the spaCy Doc.
    """
    spans: list[tuple[int, int]] = []
    prev_end = 0

    for m in TOKEN_RE.finditer(text):
        start, end = m.start(), m.end()
        gap = text[prev_end:start]

        if gap and gap != " ":
            # Multi-char whitespace → explicit whitespace span
            spans.append((prev_end, start))

        spans.append((start, end))
        prev_end = end

    # Trailing whitespace (if any)
    if prev_end < len(text):
        spans.append((prev_end, len(text)))

    return spans


def make_doc(text: str, vocab: Vocab) -> Doc:
    """Tokenize *text* and return a spaCy :class:`Doc` with correct char offsets."""
    token_spans = list(TOKEN_RE.finditer(text))

    words: list[str] = []
    spaces: list[bool] = []
    prev_end = 0

    for m in token_spans:
        start, end = m.start(), m.end()
        gap = text[prev_end:start]

        if gap == " " and words:
            # Single space after previous token
            spaces[-1] = True
        elif gap:
            # Multi-char whitespace → insert as whitespace token
            words.append(gap)
            spaces.append(False)

        words.append(m.group())
        spaces.append(False)
        prev_end = end

    # Trailing whitespace
    trailing = text[prev_end:]
    if trailing:
        if trailing == " " and words:
            spaces[-1] = True
        elif trailing:
            words.append(trailing)
            spaces.append(False)

    if not words:
        return Doc(vocab)

    return Doc(vocab, words=words, spaces=spaces)


class LatexTokenizer:
    """spaCy-compatible tokenizer that keeps LaTeX math as single tokens."""

    def __init__(self, vocab: Vocab) -> None:
        self.vocab: Vocab = vocab

    def __call__(self, text: str) -> Doc:
        return make_doc(text, self.vocab)


def create_tokenizer(nlp: Language) -> LatexTokenizer:
    """Return a :class:`LatexTokenizer` bound to *nlp*'s vocab.

    Usage::

        nlp = spacy.blank("en")
        nlp.tokenizer = create_tokenizer(nlp)
    """
    return LatexTokenizer(nlp.vocab)


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------


class JsonlEntry(TypedDict):
    """Shape of one line in the coreference jsonl fixtures."""

    text: str
    clusters: NotRequired[list[list[list[int]]]]


class Violation(TypedDict):
    """A span whose start/end does not land on a token boundary."""

    line: int
    cluster: int
    span: int
    char_span: tuple[int, int]
    text: str
    bad_start: bool
    bad_end: bool


def token_boundaries(doc: Doc) -> tuple[set[int], set[int]]:
    """Return sets of valid token-start and token-end char positions.

    Both regular tokens and whitespace tokens count: a span may legitimately
    start at a newline (e.g. a display-math block beginning with ``\\n\\n$$``).
    """
    starts = {t.idx for t in doc}
    ends = {t.idx + len(t.text) for t in doc}
    return starts, ends


def check_file(path: str, vocab: Vocab) -> list[Violation]:
    """Check that no span in *path* ends mid-token.

    Returns a list of violation dicts (empty = all good).
    """
    violations: list[Violation] = []

    with open(path) as fh:
        for line_no, line in enumerate(fh, 1):
            entry = cast(JsonlEntry, json.loads(line))
            text = entry["text"]
            doc = make_doc(text, vocab)
            starts, ends = token_boundaries(doc)

            for cluster_id, cluster in enumerate(entry.get("clusters", [])):
                for span_id, (span_start, span_end) in enumerate(cluster):
                    bad_start = span_start not in starts
                    bad_end = span_end not in ends

                    if bad_start or bad_end:
                        violations.append(
                            {
                                "line": line_no,
                                "cluster": cluster_id,
                                "span": span_id,
                                "char_span": (span_start, span_end),
                                "text": repr(text[span_start:span_end]),
                                "bad_start": bad_start,
                                "bad_end": bad_end,
                            }
                        )

    return violations


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys

    path = sys.argv[1] if len(sys.argv) > 1 else "test_fixed.jsonl"

    nlp = spacy.blank("en")
    nlp.tokenizer = LatexTokenizer(nlp.vocab)

    print(f"Checking {path} …")
    violations = check_file(path, nlp.vocab)

    if violations:
        print(f"FAIL – {len(violations)} violation(s):")
        for v in violations[:20]:
            flags: list[str] = []
            if v["bad_start"]:
                flags.append("start")
            if v["bad_end"]:
                flags.append("end")
            line = (
                f"  line {v['line']:3d}  cluster {v['cluster']}  span {v['span']}"
                f"  chars {v['char_span']}  bad={','.join(flags)}"
                f"  {v['text']}"
            )
            print(line)
        if len(violations) > 20:
            print(f"  … and {len(violations) - 20} more")
        sys.exit(1)
    else:
        print("OK – all spans align with token boundaries.")
