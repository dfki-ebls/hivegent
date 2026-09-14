import os
import re
from collections.abc import Sequence
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


class TableNode(Float):
    node_type: Literal[NodeType.Table] = NodeType.Table
    # cache rows converted to markdown, so re-chunking a table (or inspecting
    # it after chunking) does not re-parse the html
    headers: Sequence[str]
    vals: Sequence[Sequence[str]]
    raw_html: str


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
    # TODO. stub. add line numbering down the line
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


def split_node(nodeslist: list[Node], idx: int, texts_to_split_into: list[str]):
    orig_node = nodeslist[idx]
    nodeslist[idx : idx + 1] = [
        build_text_node(t, orig_node.line_numbers) for t in texts_to_split_into
    ]
