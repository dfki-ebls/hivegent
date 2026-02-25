"""Pydantic configuration models for document converters.

Each pipeline has a config model whose JSON Schema is exposed to the frontend
for generic form rendering.  Libraries that already use Pydantic (e.g. docling)
have their models reused directly; for the rest we define lightweight wrappers.
"""

from pydantic import BaseModel, Field

from cbrkit.helpers import optional_dependencies

__all__ = [
    "DoclingConverterConfig",
    "KreuzbergConverterConfig",
    "LlmConverterConfig",
    "MarkerConverterConfig",
    "MarkItDownConverterConfig",
    "MinerUConverterConfig",
    "PandocConverterConfig",
]


# --- Docling ---


class _DoclingFallbackConfig(BaseModel):
    """Placeholder used when docling is not installed."""


DoclingConverterConfig: type[BaseModel] = _DoclingFallbackConfig

with optional_dependencies():
    from docling.datamodel.pipeline_options import (
        ConvertPipelineOptions,
        ThreadedPdfPipelineOptions,
    )

    class _DoclingConverterConfig(BaseModel):
        """Configuration for the Docling conversion pipeline.

        Uses docling's own Pydantic option models.
        ``pdf_options`` applies to PDF and image formats;
        ``convert_options`` applies to Office and text formats.
        """

        pdf_options: ThreadedPdfPipelineOptions = Field(
            default_factory=ThreadedPdfPipelineOptions,
            description="Options for PDF and image formats (OCR, table structure, layout, etc.)",
        )
        convert_options: ConvertPipelineOptions = Field(
            default_factory=ConvertPipelineOptions,
            description="Options for Office and text formats (DOCX, PPTX, HTML, etc.)",
        )

    DoclingConverterConfig = _DoclingConverterConfig


# --- Marker ---


class MarkerConverterConfig(BaseModel):
    """Configuration for the Marker conversion pipeline."""


# --- Pandoc ---


class PandocConverterConfig(BaseModel):
    """Configuration for the Pandoc conversion pipeline."""

    extra_args: list[str] = Field(
        default_factory=list,
        description="Additional pandoc CLI arguments (e.g. '--wrap=none', '--toc').",
    )


# --- LLM ---


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


# --- MarkItDown ---


class MarkItDownConverterConfig(BaseModel):
    """Configuration for the MarkItDown conversion pipeline."""


# --- Kreuzberg ---


class KreuzbergConverterConfig(BaseModel):
    """Configuration for the Kreuzberg conversion pipeline."""

    force_ocr: bool = Field(
        default=False,
        description="Force OCR even when embedded text is available.",
    )
    output_format: str = Field(
        default="plain",
        description="Output format ('plain' or 'markdown').",
    )
    enable_quality_processing: bool = Field(
        default=True,
        description="Enable quality post-processing of extracted text.",
    )
    include_document_structure: bool = Field(
        default=False,
        description="Include structural elements (headings, lists) in output.",
    )


# --- MinerU ---


class MinerUConverterConfig(BaseModel):
    """Configuration for the MinerU conversion pipeline."""
