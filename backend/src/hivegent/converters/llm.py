"""LLM-based document converter using Pydantic AI with vision models."""

from dataclasses import dataclass
from pathlib import Path

from pydantic_ai import BinaryContent
from pydantic_ai.models.openai import OpenAIResponsesModel
from pydantic_ai.providers.openai import OpenAIProvider

from ..agent import base_agent
from ..types import LlmConfig
from .base import DocumentConverter

__all__ = ["LLMConverter"]

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

    async def __call__(
        self,
        path: Path,
        /,
        options: LlmConfig | None = None,
    ) -> str:
        """Convert a document to markdown using an LLM with vision capabilities.

        Args:
            path: Path to the document to convert.
            options: LLM provider options (model, api_key, base_url).

        Returns:
            The document content converted to markdown.
        """
        opts = options or LlmConfig()

        if not opts.model:
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
            [CONVERSION_PROMPT, content],
            model=OpenAIResponsesModel(
                opts.model,
                provider=OpenAIProvider(
                    api_key=opts.api_key,
                    base_url=opts.base_url,
                ),
            ),
        )

        return str(result.output)
