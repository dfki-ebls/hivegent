"""Unit tests for context-aware, per-conversion deduplicated captioning."""

import io

import pytest
from PIL import Image

from hivegent import workspace
from hivegent.converters.asset_processing import (
    image_context_windows,
    perceptual_key,
)
from hivegent.converters.base import AssetRole, ExtractedImage
from hivegent.types import AssetProcessingMode, LlmConfig


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


async def test_prepare_conversion_assets_captions_duplicates_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captioned: list[tuple[str, list[str]]] = []

    async def fake_describe(
        filepath: str,
        content: bytes,
        media_type: str,
        contexts: list[str],
        llm: LlmConfig,
    ) -> str:
        captioned.append((filepath, list(contexts)))
        return "caption\n"

    monkeypatch.setattr(workspace.prepare, "_build_image_description", fake_describe)

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

    (
        ref_mapping,
        assets,
        asset_entries,
    ) = await workspace.prepare._prepare_conversion_assets(
        "doc.assets",
        images,
        contexts,
        AssetProcessingMode.DESCRIBE,
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

    # Both figure refs point at the single representative; the decorative logo
    # keeps its own reference.
    assert ref_mapping["fig_a.png"] == "doc.assets/fig_a.png"
    assert ref_mapping["fig_b.png"] == "doc.assets/fig_a.png"
    assert ref_mapping["logo.png"] == "doc.assets/logo.png"

    # The representative figure and the decorative logo are both staged for
    # writing; only the figure also gets a caption entry.
    assert {a.path for a in assets} == {"doc.assets/fig_a.png", "doc.assets/logo.png"}
    assert len(asset_entries) == 1
    assert asset_entries[0].description_path == "doc.assets/fig_a.md"
