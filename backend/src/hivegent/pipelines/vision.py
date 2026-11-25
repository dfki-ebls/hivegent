"""The pipelines that call the operator's vision model, so they belong to the app.

`Vision` is the `exar.ingest.Describe` every vision call goes through,
used by the `llm` converter and by the captions of images and videos (`exar.ingest.caption`).
"""

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from exar.ingest import Media, Parsed, sanitize_image
from pydantic_ai import BinaryContent

from ..config import settings
from ..llm import complete
from ..llm_config import LlmConfig, aux_llm
from ..shared.formats import LLM_MEDIA_TYPES

__all__ = ["CAPTION_TIMEOUT", "Vision", "VisionParser"]

CAPTION_TIMEOUT = 120.0
"""Seconds one caption may take."""

_PROMPT = (
    "Convert this document to markdown.\n"
    "Extract all text preserving structure (headings, lists, paragraphs).\n"
    "Convert tables to markdown tables.\n"
    "Convert equations and formulas to LaTeX (inline $...$ or block $$...$$).\n"
    "Do not include any commentary, just the converted content."
)


@dataclass(frozen=True, slots=True, kw_only=True)
class Vision:
    """The vision model answering a prompt of text and media, the call bounded.

    The media come sanitized, as `exar.ingest.caption` and :class:`VisionParser` send them.

    Args:
        config: The vision model.
        timeout: Seconds the call may take across its retries.
    """

    config: LlmConfig
    timeout: float = CAPTION_TIMEOUT

    async def __call__(self, prompt: Sequence[str | Media], /) -> str:
        parts = [
            part
            if isinstance(part, str)
            else BinaryContent(data=part.data, media_type=part.media_type)
            for part in prompt
        ]

        return (
            await complete(parts, self.config, output_type=str, timeout=self.timeout)
        ).strip()


@dataclass(frozen=True, slots=True, kw_only=True)
class VisionParser:
    """Converts a document by sending it whole to the vision model.

    Args:
        prompt: The instructions of the conversion.
    """

    prompt: str = _PROMPT

    async def __call__(self, source: Path, /) -> Parsed:
        aux = aux_llm()

        if aux is None:
            raise ValueError(
                "No model configured. Set llm.aux to a small, fast, vision-capable "
                "model of the catalog."
            )

        suffix = source.suffix.lower()
        media_type = LLM_MEDIA_TYPES.get(suffix)

        if media_type is None:
            raise ValueError(f"Unsupported extension: {suffix!r}")

        # A whole document may take as long as one model request is allowed.
        vision = Vision(config=aux, timeout=settings.llm.request_timeout_seconds)
        data = sanitize_image(source.read_bytes(), media_type)
        markdown = await vision([self.prompt, Media(data=data, media_type=media_type)])

        return Parsed(markdown=markdown)
