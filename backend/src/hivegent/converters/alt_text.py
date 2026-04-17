"""Alt text generation for images using a vision model."""

import asyncio
import logging
import re
from pathlib import PurePosixPath

from pydantic_ai import BinaryContent
from pydantic_ai.models.openai import OpenAIChatModel, OpenAIChatModelSettings
from pydantic_ai.providers.openai import OpenAIProvider

from ..agents import base_agent
from ..types import LlmConfig
from .images import guess_image_media_type, sanitize_image_bytes

__all__ = ["MD_IMAGE_RE", "describe_image", "generate_alt_texts"]

logger = logging.getLogger(__name__)

MD_IMAGE_RE = re.compile(r"!\[([^\]]*)\]\(([^)]+)\)")

_ALT_TEXT_PROMPT = (
    "Describe this image in one concise sentence for use as alt text. "
    "Be factual and specific. Do not start with 'This image shows' or similar."
)

_MAX_CONCURRENCY = 8
_VISION_MODEL_SETTINGS = OpenAIChatModelSettings(openai_reasoning_effort="none")


async def describe_image(
    image_bytes: bytes,
    media_type: str,
    llm_options: LlmConfig,
) -> str:
    """Generate a description for a single image.

    PNG images are sanitized before being sent to the vision model.

    Args:
        image_bytes: The raw image bytes.
        media_type: The MIME type of the image.
        llm_options: LLM configuration with a vision model.

    Returns:
        A concise description string.

    Raises:
        OSError: If the image cannot be decoded.
        ValueError: If Pillow rejects the image.
    """
    content = BinaryContent(
        data=sanitize_image_bytes(image_bytes, media_type),
        media_type=media_type,
    )
    result = await base_agent.run(
        [_ALT_TEXT_PROMPT, content],
        model=OpenAIChatModel(
            llm_options.model,
            provider=OpenAIProvider(
                api_key=llm_options.api_key,
                base_url=llm_options.base_url,
            ),
        ),
        model_settings=_VISION_MODEL_SETTINGS,
    )
    return str(result.output).strip()

async def generate_alt_texts(
    markdown: str,
    images: dict[str, bytes],
    llm_options: LlmConfig | None,
) -> str:
    """Fill empty alt text in markdown image references using a vision model.

    Scans for ``![](path)`` patterns where alt text is empty.  For each,
    sends the image to the vision model and inserts the generated
    description.  References with existing alt text are left unchanged.
    On failure, falls back to the filename stem.

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

    descriptions: dict[str, str] = {}

    if has_vision:
        assert llm_options is not None
        semaphore = asyncio.Semaphore(_MAX_CONCURRENCY)

        async def _gen(path: str, data: bytes) -> tuple[str, str]:
            media_type = guess_image_media_type(path)
            if media_type is None:
                return path, PurePosixPath(path).stem
            async with semaphore:
                try:
                    return path, await describe_image(data, media_type, llm_options)
                except Exception:
                    logger.warning("Alt text generation failed for %s", path, exc_info=True)
                    return path, PurePosixPath(path).stem

        results = await asyncio.gather(*[_gen(p, d) for p, d in tasks.items()])
        for path, desc in results:
            descriptions[path] = desc
    else:
        for path in tasks:
            descriptions[path] = PurePosixPath(path).stem

    def _replace(m: re.Match[str]) -> str:
        alt, path = m.group(1), m.group(2)
        if alt or path not in descriptions:
            return m.group(0)
        return f"![{descriptions[path]}]({path})"

    return MD_IMAGE_RE.sub(_replace, markdown)
