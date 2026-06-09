"""LLM-based document converter using Pydantic AI with vision models."""

from dataclasses import dataclass, field
from pathlib import Path

from pydantic import BaseModel, Field
from pydantic_ai import BinaryContent
from pydantic_ai.settings import ModelSettings

from ..agents.app import base_agent
from ..llm import model_from_config
from ..types import LlmConfig
from .base import ConversionResult, DocumentConverter
from .images import sanitize_image_bytes

__all__ = ["LLMConverter", "LlmConverterConfig"]


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


MEDIA_TYPES: dict[str, str] = {
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".bmp": "image/bmp",
    ".tiff": "image/tiff",
    ".tif": "image/tiff",
}


@dataclass(slots=True, frozen=True)
class LLMConverter(DocumentConverter):
    """Document converter using vision-capable LLMs.

    This converter uses Pydantic AI with BinaryContent to send documents
    directly to vision-capable models for conversion to markdown.
    """

    name = "llm"
    label = "LLM"
    description = "Uses vision model for all files"
    extensions = frozenset(MEDIA_TYPES)
    config: LlmConverterConfig = field(default_factory=LlmConverterConfig)
    llm_options: LlmConfig = field(default_factory=LlmConfig)

    async def _convert(self, path: Path, /) -> ConversionResult:
        if not self.llm_options.model:
            raise ValueError(
                "No auxiliary model configured. "
                "Set HIVEGENT_LLM__AUX_MODEL to a small, fast, vision-capable model."
            )

        suffix = path.suffix.lower()
        media_type = MEDIA_TYPES.get(suffix)
        if media_type is None:
            raise ValueError(f"Unsupported extension: {suffix!r}")

        raw_bytes = path.read_bytes()
        content = BinaryContent(
            data=sanitize_image_bytes(raw_bytes, media_type),
            media_type=media_type,
        )

        # `thinking=False` is layered on top of the agent's default
        # ``model_settings`` (request timeout) via pydantic-ai's
        # ``merge_model_settings``; no need to restate the timeout here.
        result = await base_agent.run(
            [self.config.prompt, content],
            model=model_from_config(self.llm_options),
            model_settings=ModelSettings(thinking=False),
        )

        return ConversionResult(markdown=str(result.output))
