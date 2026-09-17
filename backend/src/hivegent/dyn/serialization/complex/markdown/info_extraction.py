import asyncio
import io
from pathlib import Path
from typing import cast

import imgkit
from dotenv import load_dotenv
from markdown2 import markdown
from PIL import Image
from pydantic_ai import Agent, BinaryContent, ImageUrl

from hivegent.dyn.commons.tabular_data import assemble_table
from hivegent.dyn.config import DEFAULT_GENERATOR
from hivegent.dyn.util import run_in_parallel

from .model import (
    Document,
    FigureNode,
    Float,
    ImagePath,
    Node,
    TableMdNode,
    TableNode,
    TextNode,
)

type SummarizeCall = tuple[str, str, Node, list[str]]


def image_to_binary_content(image: Path | Image.Image) -> BinaryContent:
    """Convert the provided image into Pydantic AI's binary content with its corresponding MIME type.

    Args:
        image (Path | Image.Image): The input image path or object

    Returns:
        BinaryContent: The binary data and media type of the image
    """
    if isinstance(image, Path):
        image = Image.open(image)

    mime_type = (
        "image/png"
        if image.format is None
        else Image.MIME.get(image.format, "image/png")
    )

    buffer = io.BytesIO()
    image.save(buffer, format=image.format)
    image_bytes = buffer.getvalue()

    return BinaryContent(data=image_bytes, media_type=mime_type)


def render_html(html_string: str, quiet: bool = False) -> Image.Image:
    """quiet keeps wkhtmltoimage's progress bars off stdout, which matters when
    the caller prints progress of its own."""
    # output_path=False makes imgkit return the rendered bytes; it only returns
    # True when it writes to a file
    img_bytes = imgkit.from_string(
        html_string, False, options={"quiet": ""} if quiet else None
    )
    assert not isinstance(img_bytes, bool)

    # 3. Load bytes into PIL
    img = Image.open(io.BytesIO(img_bytes))
    return img


async def summarize_figure_generic(
    image: ImagePath | Path | Image.Image, prompt: str, model: str
) -> str:
    """Sets a figure Node's summary to an LLM generated text.
    references is to be retrieved from float_mentions.find_mentions_spacy.
    Returns heading, Node index, Node"""

    agent = Agent(
        model,
        instructions=(
            "Extract the most important information derivable from this figure in a compact paragraph"
        ),
    )
    # fallback for tables
    if isinstance(image, Path) or isinstance(image, Image.Image):
        result = await agent.run([prompt, image_to_binary_content(image)])
    else:
        result = await agent.run(
            [
                prompt,
                ImageUrl(image.location)
                if image.is_remote
                else image_to_binary_content(image.path),
            ]
        )

    return result.output


async def summarize_table(
    title: str,
    heading: str,
    node: Node,
    references: list[str],
):
    """Generate a summary for a table based on provided context and references.

    Args:
        title (str): The title of the table
        heading (str): The section heading
        node (Node): The node containing the table data
        references (list[str]): A list of relevant references

    Returns:
        None: Updates the node with the generated summary
    """

    assert isinstance(node.data, (TableMdNode, TableNode))
    prompt = f"""PAPER TITLE: {title}
    SECTION HEADING: {heading}
    RELEVANT TEXT REFERENCES: {references}
    TABLE CAPTION: {node.data.caption}
    """

    html = (
        node.data.raw_html
        if isinstance(node.data, TableNode)
        else markdown(assemble_table("", node.data.headers, node.data.vals))
    )
    summary = await summarize_figure_generic(
        render_html(html), prompt, DEFAULT_GENERATOR
    )

    node.data.summary = summary


async def summarize_figure(
    title: str,
    heading: str,
    node: Node,
    references: list[str],
):
    """Generate a summary for the figure node.

    Args:
        title (str): The title of the figure.
        heading (str): The section heading.
        node (Node): The node containing the figure data.
        references (list[str]): A list of relevant text references.

    Returns:
        None: None
    """
    assert isinstance(node.data, FigureNode)
    prompt = f"""PAPER TITLE: {title}
    SECTION HEADING: {heading}
    RELEVANT TEXT REFERENCES: {references}
    TABLE CAPTION: {node.data.caption}
    """

    summary = await summarize_figure_generic(node.data.path, prompt, DEFAULT_GENERATOR)

    node.data.summary = summary


def summarize_all_floats(
    doc: Document,
) -> None:
    """Summarizes all floats (figures and tables) within the Document.

    Args:
        doc (Document): The document to process
    """
    _ = load_dotenv()
    params_figure: list[SummarizeCall] = []
    params_table: list[SummarizeCall] = []
    for heading, float_node in doc.all_floats:
        assert isinstance(float_node.data, Float)
        refs = doc.float_mentions.get(float_node.data.name, [])
        refs = [cast(TextNode, c.data).content for c in refs]
        if isinstance(float_node.data, FigureNode):
            params_figure.append((doc.title, heading, float_node, refs))
        else:
            params_table.append((doc.title, heading, float_node, refs))
    _ = asyncio.run(run_in_parallel(summarize_figure, params_figure))
    _ = asyncio.run(run_in_parallel(summarize_table, params_table))
