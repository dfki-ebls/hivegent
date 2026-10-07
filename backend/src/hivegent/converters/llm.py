"""LLM-based document converter using Pydantic AI with vision models."""

from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

from pydantic import BaseModel, Field
from pydantic_ai import BinaryContent

from ..config import settings
from ..llm import complete
from ..llm_config import LlmConfig
from .base import ConversionResult, DocumentConverter
from .formats import LLM_MEDIA_TYPES
from .images import sanitize_image_bytes

__all__ = ["LLMConverter", "LlmConverterConfig", "describe"]


async def describe(
    prompt: Sequence[str | BinaryContent], config: LlmConfig, *, timeout: float
) -> str:
    """Answer *prompt*, images or documents among its parts, with the vision model.

    The one policy every vision call goes through: PNG parts are sanitized
    before they are sent, and :func:`~hivegent.llm.complete` bounds the call
    to *timeout* seconds and turns thinking off.
    """
    parts = [
        BinaryContent(
            data=sanitize_image_bytes(part.data, part.media_type),
            media_type=part.media_type,
        )
        if isinstance(part, BinaryContent)
        else part
        for part in prompt
    ]

    return (await complete(parts, config, timeout=timeout)).strip()


class LlmConverterConfig(BaseModel):
    """Configuration for the LLM conversion pipeline."""

    prompt: str = Field(
        default=(
            "Convert this document to markdown.\n"
            "Extract all text preserving structure (headings, lists, paragraphs).\n"
            "Convert tables to markdown tables.\n"
            "Convert equations and formulas to LaTeX (inline $...$ or block $$...$$).\n"
            "Do not include any commentary, just the converted content."
        ),
        description="System prompt sent to the vision model for conversion.",
    )


@dataclass(slots=True, frozen=True)
class LLMConverter(DocumentConverter):
    """Document converter using vision-capable LLMs.

    This converter uses Pydantic AI with BinaryContent to send documents
    directly to vision-capable models for conversion to markdown.
    """

    name = "llm"
    config: LlmConverterConfig = field(default_factory=LlmConverterConfig)
    llm_options: LlmConfig | None = None

    async def _convert(self, path: Path, /) -> ConversionResult:
        if self.llm_options is None or not self.llm_options.model:
            raise ValueError(
                "No auxiliary model configured. "
                "Set HIVEGENT_LLM__AUX_MODEL to a small, fast, vision-capable model."
            )

        suffix = path.suffix.lower()
        media_type = LLM_MEDIA_TYPES.get(suffix)
        if media_type is None:
            raise ValueError(f"Unsupported extension: {suffix!r}")

        content = BinaryContent(data=path.read_bytes(), media_type=media_type)
        # A whole document may take as long as one model request is allowed.
        markdown = await describe(
            [self.config.prompt, content],
            self.llm_options,
            timeout=settings.llm.request_timeout_seconds,
        )

        return ConversionResult(markdown=markdown)
