import os
import re
from collections.abc import Sequence
from copy import copy
from enum import Enum
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, Field


class ImagePath(BaseModel):
    location: str
    is_remote: bool

    def __init__(self, location: str):
        remote = location.startswith(("http:/", "https:/"))
        super().__init__(
            location=location,
            is_remote=remote,
        )

    def resolve(self, base_dir: Path) -> None:
        """LLM-GENERATED. Make a relative local path absolute. Image paths in a markdown file
        are relative to that file, not to the cwd, so the document has to hand
        us its own directory."""
        if self.is_remote:
            return
        loc = Path(self.location)
        if not loc.is_absolute():
            loc = base_dir / loc
        self.location = os.path.abspath(loc)

    @property
    def path(self):
        if self.is_remote:
            raise TypeError("online images have no path!")
        return Path(self.location)


class NodeType(str, Enum):
    Text = "text"
    Figure = "figure"
    Table = "table"
    TableMd = "table_markdown"


class Float(BaseModel):
    caption: str
    summary: str = Field(default="")
    name: str = Field(default="")

    def render(self, short: bool = False) -> str:
        """short drops the caption and keeps only the summary: in papers a
        caption is regularly several times the length of the passage that
        references the float."""
        ret = f"[[{self.name if self.name else 'Figure'}"
        if self.caption and not short:
            ret += f" | Caption: {self.caption}"
        if self.summary:
            ret += f" | Summary: {self.summary}"
        ret += "]]"
        return ret


class TextNode(BaseModel):
    node_type: Literal[NodeType.Text] = NodeType.Text
    content: str


class FigureNode(Float):
    path: ImagePath
    node_type: Literal[NodeType.Figure] = NodeType.Figure


class TableMdNode(Float):
    node_type: Literal[NodeType.TableMd] = NodeType.TableMd
    headers: Sequence[str]
    vals: Sequence[Sequence[str]]
    raw_content: str


class TableNode(Float):
    node_type: Literal[NodeType.Table] = NodeType.Table
    # cache rows converted to markdown, so re-chunking a table (or inspecting
    # it after chunking) does not re-parse the html
    headers: Sequence[str]
    vals: Sequence[Sequence[str]]
    raw_content: str


NodeContent = Annotated[
    TextNode | FigureNode | TableNode | TableMdNode,
    Field(discriminator="node_type"),
]


class Node(BaseModel):
    data: NodeContent
    line_numbers: tuple[int, int]
    start_index: int = Field(default=0)
    end_index: int = Field(default=0)

    @property
    def node_type(self) -> NodeType:
        return self.data.node_type

    def append(self, other_node: "Node", do_break: bool = False):
        if not isinstance(self.data, TextNode) or not isinstance(
            other_node.data, TextNode
        ):
            raise TypeError("can only perform append on Text Nodes!")
        self.data.content += "\n" if do_break else " "
        self.data.content += other_node.data.content
        self.end_index = other_node.end_index


type ContentTree = list[tuple[str, list[Node]]]
type MentionsDict = dict[str, str]
type FloatDict = dict[str, Node]


def _has_heading(text: str) -> bool:
    return text.startswith("# ")


class Document(BaseModel):
    tree: ContentTree
    path: Path
    mentions: MentionsDict
    floats: FloatDict = Field(default={})
    float_mentions: dict[str, list[Node]] = Field(default={})
    raw_text: str

    def get_float(self, name: str) -> Node | None:
        """A subfigure reference ('Figure 2(a)') usually has no float of its own;
        fall back to the parent figure."""
        return self.floats.get(name) or self.floats.get(
            re.sub(r"\([a-zA-Z]\)$", "", name)
        )

    @property
    def title(self) -> str:
        if len(self.tree) == 0:
            return ""
        return self.tree[0][0]

    @property
    def markdown(self) -> str:
        ret = ""
        for heading, contentlist in self.tree:
            ret += "# " + heading + "\n\n"
            for c in contentlist:
                if isinstance(c.data, FigureNode):
                    ret += f"![{c.data.caption}]({c.data.path.location})\n"
                    continue
                if not isinstance(c.data, TextNode):
                    raise TypeError(f"Got {c.data}, but only TextNodes allowed!")
                ret = (
                    c.data.content
                    if _has_heading(c.data.content)
                    else ret + c.data.content
                )
                ret += c.data.content + "\n"
        return ret

    def resolve_image_paths(self, base_dir: Path) -> None:
        for _, contentlist in self.tree:
            for c in contentlist:
                if isinstance(c.data, FigureNode):
                    c.data.path.resolve(base_dir)

    @property
    def all_floats(self) -> list[tuple[str, Node]]:
        return [
            (heading, c)
            for heading, contentlist in self.tree
            for c in contentlist
            if isinstance(c.data, Float)
        ]


def build_text_node(text: str, lines: tuple[int, int]) -> Node:
    return build_node(TextNode(content=text), lines)


def build_node(content: NodeContent, lines: tuple[int, int]) -> Node:
    return Node(
        data=content,
        line_numbers=lines,
        start_index=0,
        end_index=0,
    )


def merge_nodes(nodeslist: list[Node], start_idx: int, nodes_to_merge: list[Node]):
    repr_node = nodes_to_merge[0]
    for node in nodes_to_merge[1:]:
        repr_node.append(node)
    nodeslist[start_idx : start_idx + len(nodes_to_merge)] = [repr_node]


def _find_kth_occurence(string: str, char: str, k: int) -> int:
    idx = -1
    k += 1
    for _ in range(k):
        idx = string.find(char, idx + 1)
        if idx == -1:
            return -1  # Fewer than k occurrences exist
    return idx


def _find_run(
    rows: Sequence[Sequence[str]], cell_contents: Sequence[str], y_offset: int
) -> tuple[tuple[int, int], tuple[int, int]] | None:
    matched = 0
    start = (-1, -1)
    for i, row in enumerate(rows[y_offset:]):
        for j, cell in enumerate(row):
            if cell_contents[matched] in cell:
                matched += 1
                if matched == 1:
                    start = (i, j)
                if matched == len(cell_contents):
                    return start, (i, j)
            else:
                matched = 0
                if cell_contents[matched] in cell:
                    matched += 1
                    if matched == 1:
                        start = (i, j)
                    if matched == len(cell_contents):
                        return start, (i, j)
    print(rows)
    print(cell_contents)
    return None


def _compute_indices_table(
    converted_string: str,
    table_chunk: str,
    rows: Sequence[Sequence[str]],
    y_offset: int = 0,
) -> tuple[int, int, int, int]:
    """Computes indices of a table chunks in the serialized table."""

    # extract cell contents from table_chunk
    line_offset = 0
    lines = table_chunk.splitlines()
    if lines[1].startswith("| --- "):
        # table has a header; remove it
        table_chunk = "\n".join(lines[2:])
        line_offset = 2

    table_lines = table_chunk.splitlines()
    cell_contents = [c.strip() for line in table_lines for c in line.split("|")[1:-1]]
    if len(cell_contents) < 1:
        raise ValueError(f"Not a valid table: {table_chunk}")
    y_offset = max(0, y_offset - line_offset)
    result = _find_run(rows, cell_contents, y_offset)
    assert result is not None
    start_coords, end_coords = result
    start_coords = (start_coords[0] + y_offset, start_coords[1])
    end_coords = (end_coords[0] + y_offset, end_coords[1])

    # convert table coords to indices in converted string
    lines = converted_string.splitlines()
    start_line = lines[start_coords[0] + line_offset]
    start_line_startidx = (
        _find_kth_occurence(converted_string, "\n", start_coords[0] + line_offset - 1)
        + 1
    )
    start_idx = (
        _find_kth_occurence(start_line, "|", start_coords[1]) + start_line_startidx
    )
    end_line = lines[end_coords[0] + line_offset]
    end_line_startidx = (
        _find_kth_occurence(converted_string, "\n", end_coords[0] + line_offset - 1) + 1
    )
    end_idx = (
        _find_kth_occurence(end_line, "|", end_coords[1] + 1) + end_line_startidx + 1
    )
    return (
        start_idx,
        end_idx,
        start_coords[0] + line_offset,
        end_coords[0] + line_offset,
    )


def split_table_node(nodeslist: list[Node], idx: int, texts_to_split_into: list[str]):
    orig_node = nodeslist[idx]
    orig_start = orig_node.start_index

    assert isinstance(orig_node.data, (TableNode, TableMdNode))
    orig_content = orig_node.data.raw_content

    new_nodes: list[Node] = []

    last_end = 0
    for t in texts_to_split_into:
        node = build_text_node(t, orig_node.line_numbers)
        start_idx, end_idx, start_line, end_line = _compute_indices_table(
            orig_content, t, orig_node.data.vals, last_end
        )
        node.start_index = orig_start + start_idx
        node.end_index = orig_start + end_idx
        node.line_numbers = (start_line, end_line)
        last_end = end_line
        new_nodes.append(node)
    nodeslist[idx : idx + 1] = new_nodes


def _compute_indices(
    converted_content: str, new_string: str, start_idx: int
) -> tuple[int, int]:
    new_start = converted_content.find(new_string, start_idx)
    new_end = new_start + len(new_string)
    return new_start, new_end


def split_node(nodeslist: list[Node], idx: int, texts_to_split_into: list[str]):
    orig_node = nodeslist[idx]
    orig_start = orig_node.start_index

    orig_content = (
        orig_node.data.content
        if isinstance(orig_node.data, TextNode)
        else orig_node.data.raw_content
    )

    new_nodes: list[Node] = []
    start_idx = 0

    for t in texts_to_split_into:
        node = build_text_node(t, orig_node.line_numbers)
        indices = _compute_indices(orig_content, t, start_idx)
        node.start_index = orig_start + indices[0]
        node.end_index = orig_start + indices[1]
        new_nodes.append(node)
        start_idx = indices[1] + 1
    nodeslist[idx : idx + 1] = new_nodes
