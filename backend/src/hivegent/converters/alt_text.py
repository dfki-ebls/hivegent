"""Alt text generation for images using a vision model."""

import asyncio
import io
import logging
import mimetypes
import re
from pathlib import PurePosixPath

from PIL import Image
from pydantic_ai import BinaryContent
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider

from ..agents import base_agent
from ..types import LlmConfig

__all__ = ["MD_IMAGE_RE", "describe_image", "generate_alt_texts"]

logger = logging.getLogger(__name__)

MD_IMAGE_RE = re.compile(r"!\[([^\]]*)\]\(([^)]+)\)")

_ALT_TEXT_PROMPT = (
    "Describe this image in one concise sentence for use as alt text. "
    "Be factual and specific. Do not start with 'This image shows' or similar."
)


def _strip_png_metadata(image_bytes: bytes, media_type: str) -> bytes:
    """Re-encode an image to strip metadata that may cause server errors.

    Some PNG files extracted from PDFs contain oversized text chunks that
    cause ``PngImagePlugin.MAX_TEXT_CHUNK`` errors on inference servers.
    Re-saving through Pillow drops those chunks.

    Args:
        image_bytes: The raw image bytes.
        media_type: The MIME type of the image.

    Returns:
        Clean image bytes, or the original bytes if re-encoding fails.
    """
    if media_type != "image/png":
        return image_bytes
    try:
        with Image.open(io.BytesIO(image_bytes)) as img:
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            return buf.getvalue()
    except Exception:
        return image_bytes


async def describe_image(
    image_bytes: bytes,
    media_type: str,
    llm_options: LlmConfig,
) -> str:
    """Generate a description for a single image.

    Args:
        image_bytes: The raw image bytes.
        media_type: The MIME type of the image.
        llm_options: LLM configuration with a vision model.

    Returns:
        A concise description string.
    """
    clean_bytes = _strip_png_metadata(image_bytes, media_type)
    content = BinaryContent(data=clean_bytes, media_type=media_type)
    result = await base_agent.run(
        [_ALT_TEXT_PROMPT, content],
        model=OpenAIChatModel(
            llm_options.model,
            provider=OpenAIProvider(
                api_key=llm_options.api_key,
                base_url=llm_options.base_url,
            ),
        ),
    )
    return str(result.output).strip()


def _guess_media_type(path: str) -> str | None:
    """Guess the MIME type for an image path.

    Args:
        path: Relative image path.

    Returns:
        A MIME type string, or ``None`` if unrecognized.
    """
    mt = mimetypes.guess_type(path)[0]
    if mt and mt.startswith("image/"):
        return mt
    return None


async def generate_alt_texts(
    markdown: str,
    images: dict[str, bytes],
    llm_options: LlmConfig | None,
) -> str:
    """Fill empty alt text in markdown image references using a vision model.

    Scans for ``![](path)`` patterns where alt text is empty.  For each,
    sends the image to the vision model and inserts the generated
    description.  References with existing alt text are left unchanged.

    Falls back to filename stem when no vision model is configured.

    Args:
        markdown: The markdown content with image references.
        images: Mapping of relative image paths to their binary content.
        llm_options: LLM configuration for the vision model.  When
            ``None`` or when ``model`` is empty, falls back to stem-based
            alt text.

    Returns:
        Rewritten markdown with alt text filled in.
    """
    has_vision = llm_options is not None and bool(llm_options.model)

    # Collect empty-alt references that have image data available.
    tasks: dict[str, bytes] = {}
    for m in MD_IMAGE_RE.finditer(markdown):
        alt, path = m.group(1), m.group(2)
        if alt:
            continue
        if path.startswith(("http://", "https://", "data:")):
            continue
        if path in images:
            tasks[path] = images[path]

    if not tasks:
        return markdown

    # Generate descriptions.
    descriptions: dict[str, str] = {}

    if has_vision:
        assert llm_options is not None

        async def _gen(path: str, data: bytes) -> tuple[str, str]:
            mt = _guess_media_type(path)
            if mt is None:
                return path, PurePosixPath(path).stem
            try:
                desc = await describe_image(data, mt, llm_options)
                return path, desc
            except Exception:
                logger.warning("Alt text generation failed for %s", path, exc_info=True)
                return path, PurePosixPath(path).stem

        results = await asyncio.gather(*[_gen(p, d) for p, d in tasks.items()])
        for path, desc in results:
            descriptions[path] = desc
    else:
        for path in tasks:
            descriptions[path] = PurePosixPath(path).stem

    # Rewrite markdown with generated alt text.
    def _replace(m: re.Match[str]) -> str:
        alt, path = m.group(1), m.group(2)
        if alt or path not in descriptions:
            return m.group(0)
        return f"![{descriptions[path]}]({path})"

    return MD_IMAGE_RE.sub(_replace, markdown)
