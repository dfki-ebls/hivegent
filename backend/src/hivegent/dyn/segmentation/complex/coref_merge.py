from collections.abc import Callable, Sequence
from typing import Protocol, cast

from fastcoref.modeling import FCoref  # pyright: ignore[reportMissingTypeStubs]

from hivegent.dyn.serialization.complex.markdown.model import (
    Document,
    Node,
    TextNode,
    merge_nodes,
)
from hivegent.dyn.util import get_token_count

type Mention = tuple[int, int]  # character span of a mention: (start, end)
type Cluster = Sequence[Mention]  # mentions of one entity
type TextRange = tuple[int, Sequence[str], Sequence[int]]  # start index, texts, lengths
type TextPair = tuple[int, str, str]
type CorefDetector = Callable[[list[TextPair]], list[bool]]


class _CorefPrediction(Protocol):
    """Typed view on the (untyped) `CorefResult` returned by fastcoref."""

    def get_clusters(self, as_strings: bool = ...) -> Sequence[Cluster]: ...


def _pairs(
    ranges: list[TextRange],
    max_tokens: int,
) -> list[TextPair]:
    """Generate pairs from input ranges that don't exceed the token limit.

    Args:
        ranges (list[TextRange]): list of TextRange objects defining input segments
        max_tokens (int): the maximum allowed combined length for adjacent inputs

    Returns:
        list[TextPair]: list of generated text pairs
    """
    ret: list[TextPair] = []
    for start_idx, list_input, input_lengths in ranges:
        ret += [
            (start_idx + i, list_input[i], list_input[i + 1])
            for i in range(len(list_input) - 1)
            if input_lengths[i] + input_lengths[i + 1] <= max_tokens
        ]
    return ret


def _extract_text_ranges(contentlist: list[Node]) -> list[TextRange]:
    """Extract contiguous runs of text nodes (converted to TextRanges) from a list of nodes.

    Args:
        contentlist (list[Node]): The list of nodes to process.

    Returns:
        list[TextRange]: A list of extracted text ranges
    """
    idx = 0
    current_texts: list[str] = []
    ranges: list[TextRange] = []
    while idx < len(contentlist):
        node = contentlist[idx]
        if isinstance(node.data, TextNode):
            current_texts.append(node.data.content)
        elif current_texts:
            lengths = [get_token_count(t) for t in current_texts]
            ranges.append((idx - len(current_texts), current_texts, lengths))
            current_texts = []
        idx += 1

    if current_texts:
        lengths = [get_token_count(t) for t in current_texts]
        ranges.append((idx - len(current_texts), current_texts, lengths))
    return ranges


def _build_coref_detector(model: FCoref) -> CorefDetector:
    """Return a function that detects if coreference clusters span text boundaries.

    Args:
        model (FCoref): The coreference model used for prediction.

    Returns:
        list[bool]: A list of boolean values indicating whether merging should occur at each breakpoint.
    """

    def detect(pairs: list[TextPair]) -> list[bool]:
        ret: list[bool] = []
        inputs: list[str] = [p[1] + p[2] for p in pairs]
        breakpoints: list[int] = [len(p[1]) for p in pairs]

        predictions = cast(Sequence[_CorefPrediction], model.predict(texts=inputs))
        # list of clusters per input
        all_corefs: list[Sequence[Cluster]] = [
            pred.get_clusters(as_strings=False) for pred in predictions
        ]
        for bp, clusters in zip(breakpoints, all_corefs):
            should_merge: bool = False
            # we want to merge both texts if there is a coreference
            # where an entity is reference across the border
            # i.e., one mention completely on the left side,
            # one mention completely on the right side
            for c in clusters:
                fully_left: bool = False
                fully_right: bool = False
                for mention in c:
                    if mention[0] <= bp and mention[1] <= bp:
                        fully_left = True
                    if mention[0] > bp and mention[1] > bp:
                        fully_right = True
                if fully_left and fully_right:
                    should_merge = True
                    break
            ret.append(should_merge)

        return ret

    return detect


def _coref_merge_section_inplace(
    contentlist: list[Node], limit: int, detector: CorefDetector
):
    """Merges suitable text nodes within a single section of a Document.
    Args:
        contentlist (list[Node]): The list of content nodes to process
        limit (int): The maximum allowed length for merged sections
        detector (CorefDetector): The detector used to determine coreference merges

    Returns:
        None: Modifies the contentlist in place
    """
    pairs = _pairs(_extract_text_ranges(contentlist), limit)
    do_merges = detector(pairs)
    for do_merge, pair in zip(reversed(do_merges), reversed(pairs)):
        # merge only if there is a coref across the border (do_merge=True)
        # and the resulting lengths would not exceed the limit
        # this has to be checked here again, since it can happen that we merged (10, 11) = 10
        # and then try to merge 9 to this new, bigger node
        start_idx, _, _ = pair
        contents = [
            cast(TextNode, c.data) for c in contentlist[start_idx : start_idx + 2]
        ]
        lengths = [get_token_count(c.content) for c in contents]
        if do_merge and sum(lengths) <= limit:
            merge_nodes(contentlist, start_idx, contentlist[start_idx : start_idx + 2])


def coref_merge_inplace(doc: Document, model: FCoref, limit: int):
    """Merge coreference sections in place within the Document.

    Args:
        doc (Document): The document object
        model (FCoref): The coreference model
        limit (int): The limit for merging

    Returns:
        None
    """
    detector = _build_coref_detector(model)
    tree = doc.tree
    for _, contentlist in tree:
        _coref_merge_section_inplace(contentlist, limit, detector)
