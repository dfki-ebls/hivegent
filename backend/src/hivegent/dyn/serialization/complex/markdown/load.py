import re
from collections.abc import Callable, Sequence
from enum import Enum
from pathlib import Path
from typing import Annotated, Literal

import numpy as np
from markdown_it import MarkdownIt
from markdown_it.token import Token
from mdit_py_plugins.footnote import footnote_plugin
from pydantic import BaseModel, Field

from hivegent.dyn.commons.markdown import (
    escape_stray_html_block_openers,
    escape_stray_tilde_fences,
    get_first_float_name,
)
from hivegent.dyn.commons.tabular_data import assemble_table, html_to_headers_rows

from .model import (
    ContentTree,
    Document,
    FigureNode,
    ImagePath,
    MentionsDict,
    Node,
    TableMdNode,
    TableNode,
    TextNode,
    build_node,
    build_text_node,
)


def figure_node_from_paragraph(path: str, caption: str) -> FigureNode:
    """Create a FigureNode using the provided path and caption.

    Args:
        path (str): The file path
        caption (str): The figure caption

    Returns:
        FigureNode: The newly created figure node
    """
    return FigureNode(
        caption=caption, path=ImagePath(path), name=get_first_float_name(caption)
    )


def _parse_heading(tokens: Sequence[Token], start_idx: int) -> tuple[str, int]:
    """Extracts the title text from a Markdown heading.

    Args:
        tokens (Sequence[Token]): Sequence of tokens
        start_idx (int): Starting index for token search

    Returns:
        tuple[str, int]: The last inline content and the offset
    """
    last_inline = ""
    offset = 0
    while True:
        t = tokens[start_idx + offset]
        if t.type == "inline":
            last_inline = t.content
        if t.type == "heading_close":
            return last_inline, offset
        offset += 1


def _parse_inline_contents(inline: Token) -> list[str | FigureNode]:
    """Parse the contents of an inline token into a list of strings and FigureNodes.

    Args:
        inline (Token): The token whose contents are to be parsed.

    Returns:
        list[str | FigureNode]: A list of extracted content strings and FigureNodes
    """
    i = 0
    contents: list[str | FigureNode] = []
    current_str = ""
    if inline.children is None:
        return []
    is_image = [t.type == "image" for t in inline.children]
    if not any(is_image):
        return [inline.content]
    while i < len(inline.children):
        token = inline.children[i]
        if token.type == "image":
            if current_str:
                contents.append(current_str)
                current_str = ""
            caption, path = str(token.attrs["alt"]), str(token.attrs["src"])
            contents.append(FigureNode(caption=caption, path=ImagePath(path)))

        elif token.type == "text" or token.type == "html_inline":
            current_str += token.content
        # standard link. keep in mind that a link could potentially also hold formatted text like inline_code etc
        elif token.type == "link_open":
            loc = str(token.attrs["href"])
            text = inline.children[i + 1].content
            current_str += f"[{text}]({loc})"
            i += 2
        elif token.type == "hardbreak" or token.type == "softbreak":
            current_str += "\n"
        elif (
            token.type.endswith("_close") or token.type.endswith("_open")
        ) and token.markup:
            current_str += token.markup
        elif token.type == "code_inline":
            current_str += token.markup + token.content
        else:
            raise ValueError(
                f"not handled: {inline.children[i - 1 : i + 2]}.\n{inline.children}"
            )
        i += 1
    if current_str:
        contents.append(current_str)
    return contents


def _parse_paragraph(
    tokens: Sequence[Token], start_idx: int
) -> tuple[list[str | FigureNode], int]:
    """Parse the contents of a paragraph.

    Args:
        tokens (Sequence[Token]): The sequence of tokens
        start_idx (int): The starting index for parsing

    Returns:
        tuple[list[str | FigureNode], int]: A list of parsed content and the offset
    """
    offset = 0
    ret: list[str | FigureNode] = []

    while True:
        t = tokens[start_idx + offset]
        if t.type == "inline":
            ret += _parse_inline_contents(t)
        if t.type == "paragraph_close":
            return ret, offset
        offset += 1


def _parse_html_block(
    tokens: Sequence[Token], start_idx: int
) -> tuple[TableNode | str, int]:
    """Parse an HTML block from tokens into a table structure or raw HTML.

    Args:
        tokens (Sequence[Token]): The sequence of tokens
        start_idx (int): The starting index in the token sequence

    Returns:
        tuple[TableNode | str, int]: A table node or raw HTML string and an offset
    """
    html = tokens[start_idx].content
    try:
        headers, rows = html_to_headers_rows(html)
    except ValueError:
        # no table found
        return html, 0
    # html_block is a single token -> skip offset 0 (caller adds +1)
    return TableNode(caption="", headers=headers, vals=rows, raw_content=html), 0


LIST_FORMATS: dict[str, Callable[[int, int, str], str]] = {  # indent, position, value
    "bullet_list_open": lambda i, idx, e: "\t" * i + "- " + e,
    "ordered_list_open": lambda i, idx, e: f"{'\t' * i}{idx + 1}. {e}",
}

_LIST_ITEM_REGEX = re.compile(r"^[ \t]*(?:-|\d+\.) ")


def _is_list_item(s: str) -> bool:
    """LLM-GENERATED. Return True if s starts with '- ' or '<number>. '."""
    return bool(_LIST_ITEM_REGEX.match(s))


def _parse_list(
    tokens: Sequence[Token], start_idx: int, close_token_name: str, indent: int = 0
) -> tuple[list[str], int]:
    """Parse bullet lists.

    Args:
        tokens (Sequence[Token]): The sequence of tokens
        start_idx (int): The starting index in the token sequence
        close_token_name (str): The name of the closing token
        indent (int): The current indentation level

    Returns:
        tuple[list[str], int]: A tuple containing the list contents and the final offset
    """
    list_contents: list[str] = []
    level = 1
    offset = 1
    formatter = LIST_FORMATS[tokens[start_idx].type]
    while level > 0:
        t = tokens[start_idx + offset]

        # nested list
        if t.type == "ordered_list_open" or t.type == "bullet_list_open":
            endname = t.type[: t.type.rfind("_")] + "_close"
            contents, skip = _parse_list(
                tokens, start_idx + offset, endname, indent + 1
            )
            list_contents += contents
            offset += skip + 1
            continue

        # list_item_open or paragraph_open
        if t.type.endswith("_open"):
            level += 1
        if t.type.endswith("_close"):
            level -= 1

        if t.type == "paragraph_open":
            content, skip = _parse_paragraph(tokens, start_idx + offset)
            for c in content:
                if isinstance(c, FigureNode):
                    # raise ValueError("Image not allowed in bullet list!")
                    continue
                list_contents.append(c)
            offset += skip
            continue

        if t.type == close_token_name:
            # apply formatting
            list_contents = [
                e if _is_list_item(e) else formatter(indent, i, e)
                for i, e in enumerate(list_contents)
            ]
            return list_contents, offset
        offset += 1
    raise ValueError(
        f"ended with {tokens[start_idx + offset].type} in {start_idx + offset} not {close_token_name}"
    )


def _parse_inline(tokens: Sequence[Token], start_idx: int) -> tuple[str, int]:
    """Return the content of an inline token.

    Args:
        tokens (Sequence[Token]): Sequence of Token objects
        start_idx (int): The starting index

    Returns:
        tuple[str, int]: Token content and zero
    """
    token = tokens[start_idx]
    # single token -> skip offset 0 (caller adds +1)
    return token.content, 0


def _parse_md_table(
    tokens: Sequence[Token], start_idx: int, raw_text: str
) -> tuple[TableMdNode, int]:
    """Parses a Markdown table.

    Args:
        tokens (Sequence[Token]): The sequence of tokens
        start_idx (int): The starting index in the token sequence

    Returns:
        tuple[TableMdNode, int]: A tuple containing the parsed table node and the offset
    """
    last_inline = ""
    offset = 0
    rows: list[list[str]] = []
    current_row: list[str] = []
    # table_close carries no map; the opening token spans the whole table
    table_lines = tokens[start_idx].map
    while True:
        t = tokens[start_idx + offset]
        if t.type == "inline":
            last_inline = t.content
        if t.type == "th_close" or t.type == "td_close":
            current_row.append(last_inline)
        if t.type == "tr_close":
            rows.append(current_row)
            current_row = []
        if t.type == "table_close":
            headers = rows[0]
            rows = rows[1:]
            table_md_node = TableMdNode(
                caption="",
                headers=headers,
                vals=rows,
                # kept verbatim: the node's indices are offsets into the
                # document, so re-rendering the table here would drift by
                # whatever whitespace the parser normalised away
                raw_content="\n".join(
                    raw_text.splitlines()[table_lines[0] : table_lines[1]]
                ),
            )
            return table_md_node, offset
        offset += 1


def _parse_blockquote(tokens: Sequence[Token], start_idx: int) -> tuple[str, int]:
    """Parses a blockquote.

    Args:
        tokens (Sequence[Token]): The sequence of tokens to process
        start_idx (int): The starting index in the tokens sequence

    Returns:
        tuple[str, int]: A tuple containing the accumulated blockquote text and the final offset
    """
    texts: list[str] = []
    offset = 0
    depth = 0
    while True:
        t = tokens[start_idx + offset]
        if t.type == "blockquote_open":
            depth += 1
        elif t.type == "blockquote_close":
            depth -= 1
            if depth == 0:
                return "\n".join(texts), offset
        elif t.type == "bullet_list_open" or t.type == "ordered_list_open":
            endname = t.type[: t.type.rfind("_")] + "_close"
            items, skip = _parse_list(tokens, start_idx + offset, endname)
            texts += ["> " + i for i in items]
            offset += skip + 1
            continue
        elif t.type == "paragraph_open":
            content, skip = _parse_paragraph(tokens, start_idx + offset)
            for c in content:
                if isinstance(c, FigureNode):
                    # raise TypeError("Blockquotes cannot contain images!")
                    continue
                texts.append("> " + c)
            offset += skip + 1
            continue
        offset += 1


def _parse_footnote_block(
    tokens: Sequence[Token], start_idx: int
) -> tuple[dict[str, str], int]:
    """Parse the footnote block.

    Args:
        tokens (Sequence[Token]): The sequence of tokens
        start_idx (int): The starting index for parsing

    Returns:
        tuple[dict[str, str], int]: A tuple containing footnote mentions and the offset
    """
    last_inline = ""
    offset = 0
    footnote_mentions: dict[str, str] = {}
    while True:
        t = tokens[start_idx + offset]
        if t.type == "inline":
            last_inline = t.content
        elif t.type == "footnote_anchor":
            reference = t.meta.get("label")
            if reference is not None:
                footnote_mentions[reference] = last_inline
        elif t.type == "footnote_block_close":
            return footnote_mentions, offset
        offset += 1


def _build_line_numbers(token: Token, endtoken: Token) -> tuple[int, int]:
    # TODO: Woher kommt das (endtoken.map = None)?
    if endtoken.map is None:
        return token.map
    return (token.map[0], endtoken.map[1])


def _parse(tokens: Sequence[Token], raw_text: str) -> tuple[ContentTree, MentionsDict]:
    """Parse the sequence of tokens into a content tree and collect footnote mentions.

    Args:
        tokens (Sequence[Token]): The sequence of tokens

    Returns:
        tuple[ContentTree, MentionsDict]: A tuple containing the content tree and footnote mentions
    """
    idx = 0
    current_nodes: list[Node] = []
    tree: ContentTree = []
    current_heading = ""
    footnote_mentions: MentionsDict = {}
    while idx < len(tokens):
        t = tokens[idx]
        skip = 1
        match t.type:
            case "paragraph_open":
                content, skip = _parse_paragraph(tokens, idx)
                for c in content:
                    current_nodes.append(
                        Node(
                            data=c
                            if isinstance(c, FigureNode)
                            else TextNode(content=c),
                            line_numbers=t.map,
                        )
                    )
            case "bullet_list_open":
                content, skip = _parse_list(tokens, idx, "bullet_list_close")
                end_token = tokens[idx + skip]
                current_nodes.append(
                    build_text_node(
                        "\n".join(content), _build_line_numbers(t, end_token)
                    )
                )
            case "ordered_list_open":
                content, skip = _parse_list(tokens, idx, "ordered_list_close")
                end_token = tokens[idx + skip]
                current_nodes.append(
                    build_text_node(
                        "\n".join(content), _build_line_numbers(t, end_token)
                    )
                )
            case "heading_open":
                title, skip = _parse_heading(tokens, idx)
                if current_heading or current_nodes:
                    tree.append((current_heading, current_nodes))
                current_nodes = []
                current_heading = title
            case "html_block":
                content, skip = _parse_html_block(tokens, idx)
                end_token = tokens[idx + skip]
                if isinstance(content, TableNode):
                    current_nodes.append(
                        build_node(content, _build_line_numbers(t, end_token))
                    )
                else:
                    current_nodes.append(
                        build_text_node(
                            "```" + content + "```", _build_line_numbers(t, end_token)
                        )
                    )
            case "table_open":
                tablemdnode, skip = _parse_md_table(tokens, idx, raw_text)
                end_token = tokens[idx + skip]
                current_nodes.append(
                    build_node(tablemdnode, _build_line_numbers(t, end_token))
                )
            case "blockquote_open":
                content, skip = _parse_blockquote(tokens, idx)
                end_token = tokens[idx + skip]
                current_nodes.append(
                    build_text_node(content, _build_line_numbers(t, end_token))
                )
            case "inline":
                content, skip = _parse_inline(tokens, idx)
                end_token = tokens[idx + skip]
                current_nodes.append(
                    build_text_node(content, _build_line_numbers(t, end_token))
                )
            case "fence":
                content, skip = _parse_inline(tokens, idx)
                end_token = tokens[idx + skip]
                current_nodes.append(
                    build_text_node(
                        "```" + content + "```", _build_line_numbers(t, end_token)
                    )
                )
            case "code_block":
                content, skip = _parse_inline(tokens, idx)
                end_token = tokens[idx + skip]
                current_nodes.append(
                    build_text_node(
                        "```" + content + "```", _build_line_numbers(t, end_token)
                    )
                )
            case "footnote_block_open":
                content, skip = _parse_footnote_block(tokens, idx)
                footnote_mentions |= content
            case "hr":
                # ignore. single token -> skip 0
                skip = 0
            case _:
                raise TypeError(
                    f"Unhandled token: {t.type}. idx: {idx}. last: {current_nodes[-1]}"
                )
        idx += skip + 1

    # flush content when no new heading comes anymore
    if current_heading or current_nodes:
        tree.append((current_heading, current_nodes))
    return tree, footnote_mentions


def load(input: Path | str) -> Document:
    """Load the content from a path or string, parse it as Markdown, and create a Document object.

    Args:
        input (Path | str): The path or string containing the Markdown content

    Returns:
        Document: A Document object
    """
    md = (
        MarkdownIt("commonmark", {"breaks": True, "html": True})
        .use(footnote_plugin)
        .enable("table")
        .enable("strikethrough")
    )

    path = Path("unknown")
    if isinstance(input, str):
        md_content = input
    else:
        with open(input, "r") as f:
            md_content = f.read()
            path = input

    # filter web conversion artifacts
    md_content = escape_stray_tilde_fences(md_content)
    md_content = escape_stray_html_block_openers(md_content)

    tokens = md.parse(md_content)

    tree, footnote_mentions = _parse(tokens, md_content)

    # set start and end indices
    md_line_indices = np.array(
        [len(l) + 1 for l in md_content.splitlines()]
    )  # count \n as well
    md_line_cumsum = np.cumsum(md_line_indices).tolist()
    for _, contentlist in tree:
        for i, node in enumerate(contentlist):
            lines = node.line_numbers
            # markdown-it line maps are 0-based: line L starts just past line L-1's
            # newline, and only line 0 starts at the top of the document
            start_idx = 0 if lines[0] <= 0 else md_line_cumsum[lines[0] - 1]
            end_idx = md_line_cumsum[lines[1] - 1]
            contentlist[i].start_index = start_idx
            contentlist[i].end_index = end_idx

    doc = Document(
        tree=tree, path=path, mentions=footnote_mentions, raw_text=md_content
    )
    # image paths are relative to the markdown file, so resolve them while we
    # still know where it lives
    doc.resolve_image_paths(path.parent if path.name != "unknown" else Path.cwd())
    return doc
