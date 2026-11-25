"""Tests for dependency-free pipeline registries."""

import subprocess
import sys
from dataclasses import replace
from importlib.util import find_spec

import pytest
from exar.ingest import Offloaded, RecursiveChunker

from hivegent import pipelines
from hivegent.config import settings
from hivegent.pipelines import check_pipelines, get_chunker


def test_pipeline_metadata_does_not_load_implementations() -> None:
    code = (
        "import sys\n"
        "from hivegent.pipelines import chunking_pipelines, conversion_pipelines\n"
        "chunking_pipelines()\n"
        "conversion_pipelines()\n"
        "print('chonkie' in sys.modules, 'docling' in sys.modules)\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
    )

    assert result.stdout.strip() == "False False"


def test_pipeline_options_come_from_the_operator_alone(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings.chunking, "pipelines", {"recursive": {"size": 512}})
    chunker = get_chunker("recursive")

    assert isinstance(chunker, Offloaded)
    assert chunker.func == RecursiveChunker(size=512)


@pytest.mark.parametrize(
    "options",
    [
        {"recursive": {"sise": 512}},
        {"recursiv": {}},
        {"none": {"x": 1}},
        {"recursive": {"size": 0}},
        {"slumber": {"size": 0}},
    ],
)
def test_the_boot_refuses_options_no_pipeline_takes(
    monkeypatch: pytest.MonkeyPatch, options: dict[str, dict[str, int]]
) -> None:
    """A misspelled option or pipeline, or options to one without any, stop the boot."""
    monkeypatch.setattr(settings.chunking, "pipelines", options)

    with pytest.raises(ValueError):
        check_pipelines()


def test_the_boot_refuses_an_unavailable_pipeline_it_configures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    missing = replace(
        pipelines.CHUNKERS["recursive"], dependencies=("hivegent_missing",)
    )
    monkeypatch.setattr(pipelines, "CHUNKERS", {"recursive": missing})
    monkeypatch.setattr(settings.chunking, "pipelines", {"recursive": {}})

    with pytest.raises(ValueError, match="not available"):
        check_pipelines()


@pytest.mark.skipif(find_spec("docling") is None, reason="docling extra absent")
def test_docling_extensions_are_upstream_formats() -> None:
    # DOCLING_EXTENSIONS is copied from upstream so routing stays dependency-free,
    # this stops the copy from naming a format docling no longer reads.
    from docling.datamodel.base_models import FormatToExtensions

    from hivegent.pipelines.formats import DOCLING_EXTENSIONS

    # Casefolded, as match_file_extension lowercases and upstream spells some in mixed case (".Rmd").
    upstream = {
        f".{extension}".casefold()
        for extensions in FormatToExtensions.values()
        for extension in extensions
    }

    assert DOCLING_EXTENSIONS <= upstream


def test_whole_chunking_runs_inline() -> None:
    # Descriptions are chunked whole without queueing behind conversions in the pool.
    whole = get_chunker("none")
    assert isinstance(whole, Offloaded) and whole.executor is None
