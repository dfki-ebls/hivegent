from typing import cast, final

from chonkie import Chunk, RecursiveChunker

from hivegent.dyn.serialization.complex.markdown.model import (
    Document,
    Node,
    TextNode,
    merge_nodes,
    split_node,
)
from hivegent.dyn.util import get_token_count


@final
class Chunker:
    def __init__(self, limit_split: int):
        self.limit_split = limit_split
        self.rec_char = RecursiveChunker(tokenizer="o200k_base", chunk_size=limit_split)

    def _merge_section_inplace(self, contentlist: list[Node], limit_merge: int):
        """Merge consecutive text nodes within a single section.

        Args:
            contentlist (list[Node]): The list of nodes to process
            limit_merge (int): The maximum length allowed for merging nodes

        Returns:
            None
        """
        idx = len(contentlist) - 1
        current_to_merge: list[Node] = []
        current_to_merge_length = 0
        while idx > -1:
            node = contentlist[idx]
            if isinstance(node.data, TextNode):
                node_length = get_token_count(node.data.content)
                if current_to_merge_length + node_length <= limit_merge:
                    current_to_merge.append(node)
                    current_to_merge_length += node_length

                # current node does not fit, merge previously selected nodes
                elif current_to_merge:
                    merge_nodes(contentlist, idx + 1, list(reversed(current_to_merge)))
                    current_to_merge = [node]
                    current_to_merge_length = node_length
            # interrupted by float, merge until here
            elif current_to_merge:
                merge_nodes(contentlist, idx + 1, list(reversed(current_to_merge)))
                current_to_merge = []
                current_to_merge_length = 0
            idx -= 1

        # flush remaining merge
        if current_to_merge:
            merge_nodes(contentlist, idx + 1, list(reversed(current_to_merge)))

    def _rec_split(self, text: str) -> list[str]:
        """Splits a text using recursive character-based splitting.

        Args:
            text (str): The input text

        Returns:
            list[str]: The list of text chunks
        """
        rec_chunks = cast(list[Chunk], self.rec_char(text))
        return [c.text for c in rec_chunks]

    def _split_section_inplace(self, contentlist: list[Node], limit: int):
        """Splits too long nodes in a single section.

        Args:
            contentlist (list[Node): The list of nodes to process
            limit (int): The maximum token count allowed per node

        Returns:
            None: None
        """
        idx = 0
        while idx < len(contentlist):
            node = contentlist[idx]
            if not isinstance(node.data, TextNode):
                idx += 1
                continue
            node_length = get_token_count(node.data.content)
            if node_length > limit:
                split_texts = self._rec_split(node.data.content)
                split_node(contentlist, idx, split_texts)
                idx += len(split_texts)
            else:
                idx += 1

    def prechunk_inplace(self, doc: Document, limit_merge: int, limit_split: int):
        """First merges consecutive texts in a single section, then splits too long texts to obey the
        maximum chunk size.

        Args:
            doc (Document): Document
            limit_merge (int): limit_merge
            limit_split (int): limit_split

        Returns:
            None: None
        """
        tree = doc.tree
        for _, contentlist in tree:
            self._merge_section_inplace(contentlist, limit_merge)
            self._split_section_inplace(contentlist, limit_split)
