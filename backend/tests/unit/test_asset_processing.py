"""Unit tests for context-aware, per-conversion deduplicated captioning."""

import io
from pathlib import Path

import pytest
from PIL import Image

from hivegent import workspace
from hivegent.converters.asset_processing import (
    image_context_windows,
    perceptual_key,
)
from hivegent.converters.base import AssetRole, ExtractedImage
from hivegent.store import Casebase
from hivegent.types import AssetProcessingMode, LlmConfig, PipelineSpec


def _png(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


def test_image_context_windows_collects_every_occurrence() -> None:
    md = "Open ![gear](a.png) then later see ![again](a.png) and ![logo](b.png)."
    ctx = image_context_windows(md)
    assert len(ctx["a.png"]) == 2
    assert "Alt text: gear" in ctx["a.png"][0]
    assert ctx["b.png"][0].startswith("Alt text: logo")


def test_perceptual_key_dedups_identical_and_skips_uniform() -> None:
    textured = _png(Image.radial_gradient("L").convert("RGB"))
    assert perceptual_key(textured) is not None
    assert perceptual_key(textured) == perceptual_key(textured)
    # Solid-color images carry no signal and must not be merged blindly.
    assert perceptual_key(_png(Image.new("RGB", (80, 60), (40, 40, 40)))) is None


async def test_process_conversion_assets_captions_duplicates_once(
    monkeypatch: pytest.MonkeyPatch, user_store: Casebase, tmp_path: Path
) -> None:
    captioned: list[tuple[str, list[str]]] = []
    written: list[str] = []

    async def fake_persist(
        store, workspace_dir, filepath, content, media_type, contexts, spec, llm, *, origin
    ) -> tuple[int, str, str]:
        captioned.append((filepath, list(contexts)))
        return (1, "none", f"{filepath}.md")

    def fake_write(workspace_dir: Path, filepath: str, content: bytes) -> Path:
        written.append(filepath)
        return tmp_path / filepath

    monkeypatch.setattr(workspace, "_persist_image_entry", fake_persist)
    monkeypatch.setattr(workspace, "_write_original_file", fake_write)

    dup = _png(Image.radial_gradient("L").convert("RGB"))
    images = {
        "fig_a.png": ExtractedImage(
            data=dup, role=AssetRole.INFORMATIVE, caption="Figure 1"
        ),
        "fig_b.png": ExtractedImage(data=dup, role=AssetRole.INFORMATIVE),
        "logo.png": ExtractedImage(
            data=_png(Image.new("RGB", (80, 60), (10, 10, 10))),
            role=AssetRole.DECORATIVE,
        ),
    }
    contexts = {"fig_a.png": ["near A"], "fig_b.png": ["near B"]}

    ref_mapping = await workspace._process_conversion_assets(
        user_store,
        tmp_path,
        "doc.assets",
        images,
        contexts,
        AssetProcessingMode.DESCRIBE,
        PipelineSpec(),
        LlmConfig(),
    )

    # The two identical figures collapse to one captioned representative whose
    # prompt sees every occurrence's context plus the converter caption.
    assert len(captioned) == 1
    representative, joint = captioned[0]
    assert representative == "doc.assets/fig_a.png"
    assert "near A" in joint
    assert "near B" in joint
    assert "Figure caption: Figure 1" in joint

    # Both figure refs point at the single representative; only the decorative
    # logo is stored verbatim.
    assert ref_mapping["fig_a.png"] == "doc.assets/fig_a.png"
    assert ref_mapping["fig_b.png"] == "doc.assets/fig_a.png"
    assert ref_mapping["logo.png"] == "doc.assets/logo.png"
    assert written == ["doc.assets/logo.png"]
