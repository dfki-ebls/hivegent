"""LLM-based document converter using Pydantic AI with vision models."""

from dataclasses import dataclass, field
from pathlib import Path

from pydantic import BaseModel, Field
from pydantic_ai import Agent, BinaryContent
from pydantic_ai.settings import ModelSettings

from ..config import settings
from ..llm import model_from_config, thinking_model_settings
from ..llm_config import LlmConfig
from .base import ConversionResult, DocumentConverter
from .formats import LLM_MEDIA_TYPES
from .images import sanitize_image_bytes

__all__ = ["LLMConverter", "LlmConverterConfig"]


# Deliberately not ``agents.app.base_agent``: importing it here would close the
# cycle converters.llm -> agents.app -> agents.common -> types -> converters.
# The defaults it would bring beyond these two are inapplicable anyway, since
# this run is a single tool-free completion: ``tool_timeout`` has nothing to
# bound and ``IncompleteToolCallGuard`` no tool call to catch.
_conversion_agent: Agent[None, str] = Agent(
    retries=settings.llm.retries,
    model_settings=ModelSettings(timeout=settings.llm.request_timeout_seconds),
)


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

        raw_bytes = path.read_bytes()
        content = BinaryContent(
            data=sanitize_image_bytes(raw_bytes, media_type),
            media_type=media_type,
        )

        # `thinking=False` is layered on top of the agent's default
        # ``model_settings`` (request timeout) via pydantic-ai's
        # ``merge_model_settings``; no need to restate the timeout here.
        result = await _conversion_agent.run(
            [self.config.prompt, content],
            model=model_from_config(self.llm_options),
            model_settings=thinking_model_settings(False, self.llm_options),
        )

        return ConversionResult(markdown=str(result.output))
