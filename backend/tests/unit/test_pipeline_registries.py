"""Tests for dependency-free pipeline registries."""

import subprocess
import sys
from dataclasses import replace
from importlib.util import find_spec

import pytest
from exar.ingest import Offloaded, RecursiveChunker

from hivegent import pipelines
from hivegent.pipelines import (
    check_pipelines,
    get_chunker,
    resolve_auto_pipeline,
)
from tests.conftest import Configure


def test_pipeline_metadata_and_auto_routing_load_no_implementation() -> None:
    """The registries stay dependency-free, which only a fresh interpreter can show."""
    code = (
        "import sys\n"
        "from hivegent.pipelines import chunking_pipelines, conversion_pipelines, get_converter\n"
        "chunking_pipelines()\n"
        "conversion_pipelines()\n"
        "get_converter('auto', 'report.docx')\n"
        "print([m for m in ('chonkie', 'docling', 'anydoc') if m in sys.modules])\n"
    )
    result = subprocess.run(
        [sys.executable, "-I", "-c", code], capture_output=True, text=True, check=True
    )

    assert result.stdout.strip() == "[]"


def test_pipeline_options_come_from_the_operator_alone(configure: Configure) -> None:
    configure(chunking={"pipelines": {"recursive": {"size": 512}}})
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
    configure: Configure, options: dict[str, dict[str, int]]
) -> None:
    """A misspelled option or pipeline, or options to one without any, stop the boot."""
    configure(chunking={"pipelines": options})

    with pytest.raises(ValueError):
        check_pipelines()


def test_the_boot_refuses_an_unavailable_pipeline_it_configures(
    monkeypatch: pytest.MonkeyPatch, configure: Configure
) -> None:
    missing = replace(
        pipelines.CHUNKERS["recursive"], dependencies=("hivegent_missing",)
    )
    monkeypatch.setattr(pipelines, "CHUNKERS", {"recursive": missing})
    configure(chunking={"pipelines": {"recursive": {}}})

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


def test_auto_never_routes_to_the_explicit_converters() -> None:
    routed = {resolve_auto_pipeline(name) for name in ("a.eml", "a.json", "a.jsonl")}

    assert routed.isdisjoint({"email", "json"})


async def test_coref_chunking_needs_the_decision_model(
    monkeypatch: pytest.MonkeyPatch, configure: Configure
) -> None:
    from exar.typing import Question

    from hivegent.llm_config import LlmConfig
    from hivegent.pipelines import coref

    configure(llm={"decision": None})

    assert not pipelines.CHUNKERS["markdown-coref"].available()

    async def pronoun(question: Question) -> float:
        return float("<current>\nIt " in question.text)

    monkeypatch.setattr(coref, "decision_llm", lambda: LlmConfig(model="judge"))
    monkeypatch.setattr(coref, "judge_from_config", lambda _: pronoun)
    paris = "Paris is a large city in the north of France."
    museums = "It is old and famous for its many museums."
    rome = "Rome is an old city in the middle of Italy."
    text = f"{paris}\n\n{museums}\n\n{rome}"
    chunks = await coref.MarkdownCoref(size=128, base=64)(text)

    assert [chunk.text.strip() for chunk in chunks] == [f"{paris}\n\n{museums}", rome]
