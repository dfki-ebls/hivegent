import asyncio
import io
import logging
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import cast

import imgkit
from markdown2 import markdown
from PIL import Image
from pydantic_ai import BinaryContent, ImageUrl

from hivegent.agents.app import base_agent
from hivegent.config import settings
from hivegent.dyn.commons.tabular_data import assemble_table
from hivegent.llm import model_from_config, thinking_model_settings
from hivegent.llm_config import LlmConfig, resolve_llm_config

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

_VISION_TIMEOUT_S = 120.0

logger = logging.getLogger(__name__)


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
    image: ImagePath | Path | Image.Image,
    prompt: str,
    llm_options: LlmConfig,
) -> str:
    """Sets a figure Node's summary to an LLM generated text.
    references is to be retrieved from float_mentions.find_mentions_spacy.
    Returns heading, Node index, Node"""

    prompt = (
        "Extract the most important information derivable from this figure in a compact paragraph\n"
        + prompt
    )
    # Only a remote ImagePath travels as a URL: a local one names a file on
    # this host, which the provider cannot fetch, so it is sent as bytes.
    if isinstance(image, (Path, Image.Image)):
        content = image_to_binary_content(image)
    elif image.is_remote:
        content = ImageUrl(image.location)
    else:
        content = image_to_binary_content(image.path)
    input = [prompt, content]
    result = await asyncio.wait_for(
        base_agent.run(
            input,
            model=model_from_config(llm_options),
            model_settings=thinking_model_settings(False, llm_options),
        ),
        timeout=_VISION_TIMEOUT_S,
    )
    return str(result.output).strip()


async def _summarize_with_fallback(
    label: str,
    summarize: Callable[[], Awaitable[None]],
) -> None:
    """Run *summarize*, leaving the float unsummarized on any failure.

    One figure the vision model choked on is worth less than the document, so
    a failure must not fail the conversion.  ``Float.summary`` defaults to the
    empty string and :meth:`Float.render` omits it when empty, so the float
    still renders with the caption the document itself gave it.
    """
    try:
        await summarize()
    except Exception:
        logger.warning("Float summary generation failed for %s", label, exc_info=True)


async def summarize_table(
    title: str,
    heading: str,
    node: Node,
    references: list[str],
    llm_options: LlmConfig,
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
        node.data.raw_content
        if isinstance(node.data, TableNode)
        else markdown(assemble_table("", node.data.headers, node.data.vals))
    )
    image = await asyncio.to_thread(render_html, html)
    summary = await summarize_figure_generic(image, prompt, llm_options)

    node.data.summary = summary


async def summarize_figure(
    title: str,
    heading: str,
    node: Node,
    references: list[str],
    llm_options: LlmConfig,
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

    summary = await summarize_figure_generic(node.data.path, prompt, llm_options)

    node.data.summary = summary


async def summarize_all_floats(
    doc: Document,
    llm_options: LlmConfig,
) -> None:
    """Summarizes all floats (figures and tables) within the Document.

    Args:
        doc (Document): The document to process
    """
    aux = resolve_llm_config(llm_options)
    if not aux.model:
        logger.debug("No aux model configured; skipping float summaries")
        return

    slots = asyncio.Semaphore(settings.llm.caption_concurrency)

    async def summarize(heading: str, float_node: Node) -> None:
        assert isinstance(float_node.data, Float)
        mentions = doc.float_mentions.get(float_node.data.name, [])
        refs = [cast(TextNode, c.data).content for c in mentions]
        summarize_one = (
            summarize_figure
            if isinstance(float_node.data, FigureNode)
            else summarize_table
        )
        async with slots:
            await _summarize_with_fallback(
                float_node.data.name or heading,
                lambda: summarize_one(doc.title, heading, float_node, refs, aux),
            )

    _ = await asyncio.gather(
        *(summarize(heading, float_node) for heading, float_node in doc.all_floats)
    )
