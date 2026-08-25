"""Tests for dependency-free pipeline registries."""

import subprocess
import sys
from importlib.util import find_spec

import pytest

from hivegent.chunkers import (
    ChunkingPipeline,
    get_chunking_pipeline_config,
)


def test_pipeline_metadata_does_not_load_implementations() -> None:
    code = (
        "import sys\n"
        "from hivegent.chunkers import get_chunking_pipelines_info\n"
        "from hivegent.converters import get_conversion_pipelines_info\n"
        "get_chunking_pipelines_info()\n"
        "get_conversion_pipelines_info()\n"
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


def test_pipeline_config_loads_only_the_selected_implementation() -> None:
    config = get_chunking_pipeline_config(ChunkingPipeline.RECURSIVE)

    assert "chunk_size" in config.schema["properties"]
    assert config.defaults["chunk_size"] == 2048


@pytest.mark.skipif(find_spec("docling") is None, reason="docling extra absent")
def test_docling_extension_snapshot_matches_upstream() -> None:
    # DOCLING_EXTENSIONS is copied from upstream so routing stays dependency-free;
    # this is what stops the copy from silently drifting.
    from docling.datamodel.base_models import FormatToExtensions

    from hivegent.converters.formats import DOCLING_EXTENSIONS

    # Casefolded on both sides: match_file_extension lowercases before lookup,
    # and upstream spells a few entries in mixed case (".Rmd").
    upstream = {
        f".{ext}".casefold() for exts in FormatToExtensions.values() for ext in exts
    }

    assert DOCLING_EXTENSIONS == upstream
