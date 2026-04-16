"""LLM-based document converter using Pydantic AI with vision models."""

from dataclasses import dataclass, field
from pathlib import Path

from pydantic import BaseModel, Field
from pydantic_ai import BinaryContent
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider

from ..agents import base_agent
from ..types import LlmConfig
from .base import ConversionResult, DocumentConverter

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

CONVERSION_PROMPT = """Convert this document to markdown.
Extract all text preserving structure (headings, lists, paragraphs).
Convert tables to markdown tables.
Convert equations and formulas to LaTeX (inline $...$ or block $$...$$).
Do not include any commentary, just the converted content."""


# Extensions derived from the MEDIA_TYPES keys above.
# https://platform.openai.com/docs/guides/pdf-files
@dataclass(slots=True, frozen=True)
class LLMConverter(DocumentConverter):
    """Document converter using vision-capable LLMs.

    This converter uses Pydantic AI with BinaryContent to send documents
    directly to vision-capable models for conversion to markdown.
    """

    name = "llm"
    extensions = frozenset(MEDIA_TYPES)
    config: LlmConverterConfig = field(default_factory=LlmConverterConfig)
    llm_options: LlmConfig = field(default_factory=LlmConfig)

    async def __call__(
        self,
        path: Path,
        /,
    ) -> ConversionResult:
        """Convert a document to markdown using an LLM with vision capabilities.

        Args:
            path: Path to the document to convert.

        Returns:
            The conversion result with markdown content.
        """
        if not self.llm_options.model:
            raise ValueError(
                "No vision model configured. "
                "Set HIVEGENT_LLM__VISION_MODEL or provide x-vision-model header."
            )

        suffix = path.suffix.lower()
        media_type = MEDIA_TYPES.get(suffix)
        assert media_type is not None, f"Unsupported extension: {suffix}"

        content = BinaryContent(
            data=path.read_bytes(),
            media_type=media_type,
        )

        result = await base_agent.run(
            [self.config.prompt, content],
            model=OpenAIChatModel(
                self.llm_options.model,
                provider=OpenAIProvider(
                    api_key=self.llm_options.api_key,
                    base_url=self.llm_options.base_url,
                ),
            ),
        )

        return ConversionResult(markdown=str(result.output))
