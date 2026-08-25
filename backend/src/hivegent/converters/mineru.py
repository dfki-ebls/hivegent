"""MinerU-based document converter (3.x ``do_parse`` pipeline backend)."""

import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from mineru.cli.common import (  # type: ignore[import-not-found]  # pyright: ignore[reportMissingImports]  # ty: ignore[unresolved-import]
    do_parse,
    read_fn,
)
from pydantic import BaseModel

from ..config import settings
from .base import ConversionResult, DocumentConverter, collect_dir_images

__all__ = ["MinerUConverter", "MinerUConverterConfig"]


# MinerU's pipeline OCR selects a recognition model by script group, not by
# individual language. The "ch" model covers Latin scripts (German, English,
# ...) alongside CJK, so it is a safe single choice for our corpus; MinerU's
# per-language codes buy nothing here, so ``ocr.languages`` is not consulted
# (see ``mineru.utils.ocr_language``).
_OCR_LANG = "ch"


def _apply_compute_env(device: str) -> None:
    """Translate the shared compute settings onto MinerU's env-var knobs.

    MinerU is configured through process env vars read at model-build time.
    ``MINERU_INTRA_OP_NUM_THREADS`` caps the CPU-side threads; ``device`` is
    forwarded to ``MINERU_DEVICE_MODE`` only when pinned, leaving ``"auto"``
    to MinerU's own detection.  Its page batching keys off VRAM
    (``MINERU_VIRTUAL_VRAM_SIZE``) rather than a page count, so
    ``compute.batch_size`` does not map.
    """
    os.environ["MINERU_INTRA_OP_NUM_THREADS"] = str(settings.compute.threads_per_worker)

    if device != "auto":
        os.environ["MINERU_DEVICE_MODE"] = device


class MinerUConverterConfig(BaseModel):
    """Configuration for the MinerU conversion pipeline."""


@dataclass(slots=True, frozen=True)
class MinerUConverter(DocumentConverter):
    """Document converter using the MinerU library (pipeline backend).

    MinerU provides high-quality PDF parsing and document conversion. Note that
    MinerU does NOT support XLSX files.
    """

    name = "mineru"
    config: MinerUConverterConfig = field(default_factory=MinerUConverterConfig)
    device: str = field(default="auto", kw_only=True)
    """Compute device for the models (``"auto"`` self-detects); code-level, not a setting."""

    def _convert_sync(self, path: Path) -> ConversionResult:
        """Run the synchronous MinerU conversion."""
        _apply_compute_env(self.device)
        name = path.stem
        with tempfile.TemporaryDirectory() as temp_dir:
            # ``do_parse`` writes ``<output_dir>/<name>/<parse_method>/<name>.md``
            # plus an ``images/`` sibling; we only need those two outputs.
            do_parse(
                output_dir=temp_dir,
                pdf_file_names=[name],
                pdf_bytes_list=[read_fn(path)],
                p_lang_list=[_OCR_LANG],
                backend="pipeline",
                f_draw_layout_bbox=False,
                f_draw_span_bbox=False,
                f_dump_middle_json=False,
                f_dump_model_output=False,
                f_dump_orig_pdf=False,
                f_dump_content_list=False,
            )
            # The parse_method ("auto") resolves to an "ocr"/"txt" subdir, so
            # locate the markdown by name rather than hardcoding the path.
            md_path = next(p for p in Path(temp_dir).rglob("*.md") if p.stem == name)
            md_dir = md_path.parent
            # MinerU references images as ``images/<hash>.jpg`` relative to the
            # markdown, which matches the keys collect_dir_images produces.
            image_data = collect_dir_images(md_dir / "images", md_dir)
            return ConversionResult(
                markdown=md_path.read_text(encoding="utf-8"), images=image_data
            )
