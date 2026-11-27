"""The conversion and chunking pipelines, keyed by the id a document records.

A pipeline is an `exar.ingest` parser or chunker, or one of the app's own that call the operator's models,
built with the operator's options for it (`conversion.pipelines`, `chunking.pipelines`)
over what the host tells every pipeline (`HOST`).
The registries merge the built-in pipelines with every enabled plugin's.
Every registration is static metadata and exar imports each backend on its first call,
so listing and routing import no backend.
The synchronous exar components run in the worker pool (`POOL`), which isolates their native crashes.

`auto` is no registration but a choice made per file: conversion routes by
the extensions each converter declares for it, with a fallback for the documents its first choice mishandles,
and chunking takes `DEFAULT_CHUNKING`.
"""

import logging
import re
import threading
from collections.abc import Callable, Mapping
from concurrent.futures import Executor, Future
from dataclasses import dataclass, replace
from functools import cache, partial
from pathlib import Path, PurePosixPath
from typing import Any

from exar.ingest import (
    Anydoc,
    AsyncChunker,
    AsyncParser,
    Docling,
    Email,
    Host,
    Json,
    Kreuzberg,
    MarkdownChunker,
    MarkItDown,
    Offloaded,
    Pandoc,
    Parsed,
    Parser,
    PdfInspector,
    PdfOxide,
    Pdftotext,
    PlainText,
    ProcessPool,
    RecursiveChunker,
    SemanticChunker,
    SentenceChunker,
    SyncComponent,
    TableChunker,
    TokenChunker,
    Whole,
    configure,
    fingerprint,
    garbled,
    limit_worker,
    offloaded,
)
from pydantic import BaseModel

from ..config import settings
from ..llm_config import decision_llm
from ..plugins.loader import LoadedPlugin, merge
from ..shared.formats import LLM_MEDIA_TYPES, match_file_extension
from ..shared.l10n import Localized
from ..shared.pipeline_registry import ChunkerPipeline, ConverterPipeline, Pipeline
from .formats import (
    ANYDOC_EXTENSIONS,
    DOCLING_EXTENSIONS,
    KREUZBERG_EXTENSIONS,
    KREUZBERG_MARKUP_EXTENSIONS,
    MARKITDOWN_EXTENSIONS,
    PANDOC_EXTENSIONS,
    PANDOC_FORMAT_OVERRIDES,
    PLAIN_TEXT_EXTENSIONS,
)
from .vision import VisionParser

__all__ = [
    "AUTO",
    "CHUNKERS",
    "CONVERTERS",
    "DEFAULT_CHUNKING",
    "HOST",
    "NO_CHUNKING",
    "PLAIN_TEXT",
    "POOL",
    "PipelineInfo",
    "check_pipelines",
    "chunker_fingerprint",
    "chunking_pipelines",
    "conversion_pipelines",
    "get_chunker",
    "get_converter",
    "plain_text",
    "resolve_auto_pipeline",
]

logger = logging.getLogger(__name__)

AUTO = "auto"
"""The pipeline chosen per file rather than registered."""

PLAIN_TEXT = "plain-text"
"""The converter AUTO falls back to, which decides from the bytes."""

NO_CHUNKING = "none"
"""The chunker that keeps a document whole, for generated descriptions."""

DEFAULT_CHUNKING = "markdown"
"""What AUTO chunks with.

Every converter yields Markdown, which tree-sitter parses fast enough that the
chunks follow its sections and blocks for documents of any size.
"""

_ID = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
"""A pipeline id, kebab-case since it is persisted and shown."""


class _Workers(Executor):
    """The worker processes of a lifespan, a fresh `ProcessPool` for the first task after a shutdown.

    The pipelines are built once with this executor, while each server lifespan
    shuts down the pool its tasks started.
    """

    def __init__(self) -> None:
        self._pool: ProcessPool | None = None
        self._lock = threading.Lock()

    def submit[**P, R](
        self, fn: Callable[P, R], /, *args: P.args, **kwargs: P.kwargs
    ) -> Future[R]:
        """Schedules a call on the pool of the current lifespan, starting it if there is none."""
        with self._lock:
            if self._pool is None:
                self._pool = ProcessPool(
                    settings.compute.worker_processes,
                    timeout=settings.compute.task_timeout_seconds,
                    max_tasks=settings.compute.worker_max_tasks,
                    initializer=partial(
                        limit_worker,
                        settings.compute.threads_per_worker,
                        settings.limits.max_image_pixels,
                    ),
                )

            return self._pool.submit(fn, *args, **kwargs)

    def shutdown(self, wait: bool = True, *, cancel_futures: bool = False) -> None:
        """Shuts down the pool of the current lifespan, a no-op if no task started one."""
        with self._lock:
            pool, self._pool = self._pool, None

        if pool is not None:
            pool.shutdown(wait, cancel_futures=cancel_futures)


POOL = _Workers()
"""The persistent worker processes of the synchronous pipelines, whose models stay warm in them.

A task that hangs or crashes takes its worker down, which the next task replaces.
"""


HOST = Host(
    executor=POOL,
    threads=settings.compute.threads_per_worker,
    batch_size=settings.compute.batch_size,
    max_image_pixels=settings.limits.max_image_pixels,
    ocr_languages=tuple(settings.conversion.ocr.languages),
    skip_ocr_with_text=settings.conversion.ocr.skip_native_text,
    extract_assets=settings.conversion.extract_assets,
    device=settings.compute.device,
)
"""What the server tells every pipeline it builds."""


def _registry[R: Pipeline[Any]](
    kind: str,
    built_in: Mapping[str, R],
    contributed: Callable[[LoadedPlugin], Mapping[str, R]],
) -> Mapping[str, R]:
    """*built_in* merged with every plugin's pipelines of *kind*.

    Raises:
        ValueError: For a duplicate id, `auto`, or one that is not kebab-case.
    """
    registry = merge(kind, built_in.items(), lambda loaded: contributed(loaded).items())

    if malformed := sorted(
        name for name in registry if name == AUTO or not _ID.fullmatch(name)
    ):
        raise ValueError(f"The {kind} ids are reserved or malformed: {malformed}")

    return registry


def _vision(options: Mapping[str, Any], host: Host) -> AsyncParser:
    return configure(VisionParser, options, host)


def _threaded[A, R](
    cls: type[SyncComponent[A, R]], /, **defaults: Any
) -> Callable[[Mapping[str, Any], Host], Offloaded[A, R]]:
    """Builds the cheap *cls* with *defaults* to run in a thread rather than a worker process.

    So it never queues behind conversions or counts toward a worker's
    `worker_max_tasks`, whose replacement reloads the conversion models.
    """
    build = offloaded(cls, **defaults)

    return lambda options, host: build(options, replace(host, executor=None))


def _coref(options: Mapping[str, Any], host: Host) -> AsyncChunker:
    from .coref import MarkdownCoref

    return configure(MarkdownCoref, options, host)


def _slumber(options: Mapping[str, Any], host: Host) -> AsyncChunker:
    # Imported on use, since chonkie is too heavy for listing the pipelines.
    from .slumber import Slumber

    return configure(Slumber, options, host)


# AUTO tries the converters declaring `auto_extensions` in this order,
# so plain text, docling, anydoc, and kreuzberg lead.
CONVERTERS: Mapping[str, ConverterPipeline] = _registry(
    "converter",
    {
        PLAIN_TEXT: ConverterPipeline(
            label=Localized(en="Plain Text", de="Reiner Text"),
            description=Localized(
                en="Text, configuration, data-serialization, and source files as-is",
                de="Text-, Konfigurations-, Datenserialisierungs- und Quelldateien unverändert",
            ),
            build=offloaded(PlainText),
            auto_extensions=PLAIN_TEXT_EXTENSIONS,
        ),
        "docling": ConverterPipeline(
            label=Localized(en="Docling", de="Docling"),
            description=Localized(
                en="Best for Office documents", de="Ideal für Office-Dokumente"
            ),
            build=offloaded(Docling),
            extensions=DOCLING_EXTENSIONS,
            auto_extensions=DOCLING_EXTENSIONS,
            dependencies=("docling", "tesserocr"),
        ),
        "anydoc": ConverterPipeline(
            label=Localized(en="anydoc", de="anydoc"),
            description=Localized(
                en="Fast structural converter for Office, OpenDocument, RTF, EPUB, and CSV",
                de="Schneller struktureller Konverter für Office, OpenDocument, RTF, EPUB und CSV",
            ),
            build=offloaded(Anydoc),
            extensions=ANYDOC_EXTENSIONS,
            auto_extensions=ANYDOC_EXTENSIONS,
            dependencies=("anydoc",),
        ),
        "kreuzberg": ConverterPipeline(
            label=Localized(en="Kreuzberg", de="Kreuzberg"),
            description=Localized(
                en="Text extraction from 75+ formats with OCR support",
                de="Textextraktion aus über 75 Formaten mit OCR-Unterstützung",
            ),
            build=offloaded(Kreuzberg),
            extensions=KREUZBERG_EXTENSIONS,
            auto_extensions=KREUZBERG_MARKUP_EXTENSIONS,
            dependencies=("kreuzberg",),
        ),
        # An explicit choice only, never AUTO's: the Nix builds leave pandoc out,
        # so AUTO would route differently in development and production.
        "pandoc": ConverterPipeline(
            label=Localized(en="Pandoc", de="Pandoc"),
            description=Localized(
                en="Universal converter for ODT, RST, RTF, EPUB, LaTeX, Org, DocBook, Typst, and more",
                de="Universeller Konverter für ODT, RST, RTF, EPUB, LaTeX, Org, DocBook, Typst und mehr",
            ),
            build=offloaded(Pandoc, formats=PANDOC_FORMAT_OVERRIDES),
            extensions=PANDOC_EXTENSIONS,
            executables=("pandoc",),
        ),
        "markitdown": ConverterPipeline(
            label=Localized(en="MarkItDown", de="MarkItDown"),
            description=Localized(
                en="Microsoft's converter for Office, PDF, images, and more",
                de="Konverter von Microsoft für Office, PDF, Bilder und mehr",
            ),
            build=offloaded(MarkItDown),
            extensions=MARKITDOWN_EXTENSIONS,
            dependencies=("markitdown",),
        ),
        "pdf-inspector": ConverterPipeline(
            label=Localized(en="pdf-inspector", de="pdf-inspector"),
            description=Localized(
                en="Fast layout-aware PDF to markdown converter, no OCR",
                de="Schneller, layoutbewusster Konverter von PDF zu Markdown, ohne OCR",
            ),
            build=offloaded(PdfInspector),
            extensions=frozenset({".pdf"}),
            dependencies=("pdf_inspector",),
        ),
        "pdf-oxide": ConverterPipeline(
            label=Localized(en="pdf_oxide", de="pdf_oxide"),
            description=Localized(
                en="High-performance Rust-based PDF to markdown converter",
                de="Leistungsstarker, Rust-basierter Konverter von PDF zu Markdown",
            ),
            build=offloaded(PdfOxide),
            extensions=frozenset({".pdf"}),
            dependencies=("pdf_oxide",),
        ),
        # Explicit choices only, never AUTO's, which routes these formats elsewhere.
        "email": ConverterPipeline(
            label=Localized(en="Email", de="E-Mail"),
            description=Localized(
                en="Emails with their subject, senders, recipients, date, body, and attachment names",
                de="E-Mails mit Betreff, Absendern, Empfängern, Datum, Text und Namen der Anhänge",
            ),
            build=_threaded(Email),
            extensions=frozenset({".eml"}),
            dependencies=("markdownify",),
        ),
        "json": ConverterPipeline(
            label=Localized(en="JSON Tables", de="JSON-Tabellen"),
            description=Localized(
                en="JSON and JSON Lines as tables, a row per object and a column per key",
                de="JSON und JSON Lines als Tabellen, eine Zeile je Objekt und eine Spalte je Schlüssel",
            ),
            build=_threaded(Json),
            extensions=frozenset({".json", ".jsonl", ".ndjson"}),
        ),
        "llm": ConverterPipeline(
            label=Localized(en="LLM", de="LLM"),
            description=Localized(
                en="Uses vision model for all files",
                de="Verwendet das Vision-Modell für alle Dateien",
            ),
            build=_vision,
            extensions=frozenset(LLM_MEDIA_TYPES),
        ),
    },
    lambda loaded: loaded.plugin.converters,
)
"""Every converter by id."""

CHUNKERS: Mapping[str, ChunkerPipeline] = _registry(
    "chunker",
    {
        NO_CHUNKING: Pipeline(
            label=Localized(en="None", de="Keine"),
            description=Localized(
                en="Keep the full document as a single chunk",
                de="Behält das gesamte Dokument als einen einzigen Chunk",
            ),
            build=_threaded(Whole),
        ),
        "token": Pipeline(
            label=Localized(en="Token", de="Token"),
            description=Localized(
                en="Fixed token-count chunks for uniform processing",
                de="Chunks mit fester Tokenanzahl für eine gleichmäßige Verarbeitung",
            ),
            build=_threaded(TokenChunker),
        ),
        "sentence": Pipeline(
            label=Localized(en="Sentence", de="Satz"),
            description=Localized(
                en="Respects sentence boundaries, good for prose and plain text",
                de="Berücksichtigt Satzgrenzen, gut für Fließtext und reinen Text",
            ),
            build=_threaded(SentenceChunker),
        ),
        "table": Pipeline(
            label=Localized(en="Table", de="Tabelle"),
            description=Localized(
                en="Splits a table into chunks of rows that repeat its header, good for spreadsheets",
                de="Teilt eine Tabelle in Chunks aus Zeilen, die ihre Kopfzeile wiederholen, gut für Tabellenkalkulationen",
            ),
            build=_threaded(TableChunker),
        ),
        "recursive": Pipeline(
            label=Localized(en="Recursive", de="Rekursiv"),
            description=Localized(
                en="Splits by paragraphs, then sentences, then words, until chunks fit",
                de="Teilt nach Absätzen, dann Sätzen, dann Wörtern, bis die Chunks passen",
            ),
            build=_threaded(RecursiveChunker),
        ),
        "markdown": Pipeline(
            label=Localized(en="Markdown", de="Markdown"),
            description=Localized(
                en="Splits Markdown by its sections and blocks, such as paragraphs, lists, and tables",
                de="Teilt Markdown nach Abschnitten und Blöcken wie Absätzen, Listen und Tabellen",
            ),
            build=_threaded(MarkdownChunker),
        ),
        "markdown-context": Pipeline(
            label=Localized(en="Markdown with Context", de="Markdown mit Kontext"),
            description=Localized(
                en="Like Markdown, every chunk starting with the headings of its sections so it reads on its own",
                de="Wie Markdown, wobei jeder Chunk mit den Überschriften seiner Abschnitte beginnt und so für sich verständlich ist",
            ),
            build=_threaded(MarkdownChunker, context=True),
        ),
        # Only once the operator opts in with `llm.decision`, which judges every boundary.
        "markdown-coref": Pipeline(
            label=Localized(
                en="Markdown with Coreference", de="Markdown mit Koreferenz"
            ),
            description=Localized(
                en="Like Markdown, joining chunks that refer back to the one before them, as the decision model judges",
                de="Wie Markdown, wobei Chunks, die sich auf den vorherigen beziehen, nach Urteil des Entscheidungsmodells verbunden werden",
            ),
            build=_coref,
            requires=lambda: decision_llm() is not None,
        ),
        "semantic": Pipeline(
            label=Localized(en="Semantic", de="Semantisch"),
            description=Localized(
                en="Splits by semantic similarity using embeddings",
                de="Teilt nach semantischer Ähnlichkeit mithilfe von Embeddings",
            ),
            build=offloaded(SemanticChunker),
            dependencies=("model2vec",),
        ),
        "slumber": Pipeline(
            label=Localized(en="Slumber", de="Slumber"),
            description=Localized(
                en="LLM-guided intelligent chunk boundary decisions",
                de="Intelligente Chunkgrenzen, vom LLM bestimmt",
            ),
            build=_slumber,
        ),
    },
    lambda loaded: loaded.plugin.chunkers,
)
"""Every chunker by id."""


class PipelineInfo(BaseModel):
    """An installed pipeline as the admin pages list it, in the request's language."""

    id: str
    label: str
    description: str


_AUTO = Localized(en="Auto", de="Automatisch")


def _listing(
    registry: Mapping[str, Pipeline[Any]], auto: Localized[str]
) -> list[PipelineInfo]:
    """The installed pipelines of *registry*, AUTO first, without importing them."""
    return [
        PipelineInfo(id=AUTO, label=_AUTO.current, description=auto.current),
        *(
            PipelineInfo(
                id=name,
                label=registration.label.current,
                description=registration.description.current,
            )
            for name, registration in registry.items()
            if registration.available()
        ),
    ]


def conversion_pipelines() -> list[PipelineInfo]:
    """The installed conversion pipelines, `auto` first."""
    return _listing(
        CONVERTERS,
        Localized(
            en="Automatically selects the best pipeline for each file",
            de="Wählt automatisch die beste Pipeline für jede Datei",
        ),
    )


def chunking_pipelines() -> list[PipelineInfo]:
    """The installed chunking pipelines, `auto` first."""
    return _listing(
        CHUNKERS,
        Localized(
            en="Recommended default: structure-aware recursive splitting",
            de="Empfohlener Standard: strukturbewusste rekursive Aufteilung",
        ),
    )


def _not_available(stage: Localized[str], name: str) -> Localized[str]:
    return Localized(
        en=f"{stage.en} pipeline '{name}' is not available",
        de=f"Die {stage.de}pipeline „{name}“ ist nicht verfügbar",
    )


_CONVERSION = Localized(en="Conversion", de="Konvertierungs")
_CHUNKING = Localized(en="Chunking", de="Chunking-")


def _unsupported_extension(name: str, extension: str, supported: str) -> Localized[str]:
    return Localized(
        en=(
            f"Conversion pipeline '{name}' does not support "
            f"{extension}. Supported: {supported}"
        ),
        de=(
            f"Die Konvertierungspipeline „{name}“ unterstützt {extension} nicht. "
            f"Unterstützt: {supported}"
        ),
    )


_AUTO_EXTENSIONS: frozenset[str] = frozenset().union(
    *(reg.auto_extensions for reg in CONVERTERS.values())
)
"""Every extension some converter declares for AUTO."""


def resolve_auto_pipeline(filename: str) -> str:
    """Resolve AUTO to the first installed converter claiming the file's extension.

    Falls back to `PLAIN_TEXT` for an extension no installed converter declares,
    such as a source file, unusual config format, or name with no suffix.  AUTO
    preparation then decides from the content whether to index it as plain
    text or create a binary stub.
    """
    extension = match_file_extension(filename, _AUTO_EXTENSIONS)

    return next(
        (
            name
            for name, reg in CONVERTERS.items()
            if extension in reg.auto_extensions and reg.available()
        ),
        PLAIN_TEXT,
    )


_RECOVERY: Mapping[str, tuple[Parser, Callable[[Parsed], bool] | None]] = {
    # Office files whose embedded parts docling loads too strictly, which anydoc reads leniently.
    **dict.fromkeys(ANYDOC_EXTENSIONS & DOCLING_EXTENSIONS, (Anydoc(), None)),
    # Legacy PDFs without a ToUnicode map, whose text poppler rebuilds from the glyph names.
    ".pdf": (Pdftotext(), garbled),
}
"""The fallback of AUTO by extension, with what output of its first choice counts as degraded.

A first choice that raises, crashes its worker, or times out always falls back.
"""


@dataclass(frozen=True, slots=True)
class _Recovering:
    """*primary* with *fallback* when it fails or its output is *degraded*, each running as a task of its own.

    Unlike `exar.ingest.WithFallback` inside one worker task, a crash or timeout of the primary
    worker is a failure here too, so the fallback still gets its isolated worker.
    The primary outcome is kept if the fallback fails too or yields no text, so its error is raised.
    """

    primary: AsyncParser
    fallback: AsyncParser
    degraded: Callable[[Parsed], bool] | None

    def _triggers(self, outcome: Parsed | Exception) -> bool:
        if isinstance(outcome, Exception):
            return True

        return self.degraded is not None and self.degraded(outcome)

    async def __call__(self, source: Path, /) -> Parsed:
        outcome: Parsed | Exception

        try:
            outcome = await self.primary(source)
        except Exception as error:  # noqa: BLE001
            outcome = error

        if self._triggers(outcome):
            try:
                recovered = await self.fallback(source)
            except Exception as error:
                if isinstance(outcome, Exception):
                    raise outcome from error
            else:
                if recovered.markdown.strip():
                    logger.warning(
                        "Fallback parsed %s where the primary parser %s",
                        source.name,
                        "failed" if isinstance(outcome, Exception) else "degraded",
                        exc_info=outcome if isinstance(outcome, Exception) else None,
                    )

                    return recovered

        if isinstance(outcome, Exception):
            raise outcome

        return outcome


def _recovering(converter: AsyncParser, filename: str) -> AsyncParser:
    """*converter* with the fallback of AUTO for *filename*, if any."""
    recovery = _RECOVERY.get(PurePosixPath(filename).suffix.lower())

    if recovery is None or not isinstance(converter, Offloaded):
        return converter

    fallback, degraded = recovery

    if converter.func == fallback:
        return converter

    return _Recovering(converter, Offloaded(fallback, converter.executor), degraded)


def get_converter(
    pipeline: str, filename: str = "", classify_assets: bool = False
) -> AsyncParser:
    """Build the converter *pipeline* names, resolving AUTO by *filename*.

    Args:
        pipeline: The conversion pipeline id, or AUTO.
        filename: The document filename, which AUTO routes by.
        classify_assets: Whether to classify the extracted assets for their role,
            which converters may skip otherwise.

    Raises:
        ValidationError: If the operator's options are invalid for the pipeline.
        ValueError: If the pipeline is unavailable or does not read the file.
    """
    host = replace(HOST, classify_assets=classify_assets)
    name = resolve_auto_pipeline(filename) if pipeline == AUTO else pipeline
    registration = CONVERTERS[name]

    if not registration.available():
        raise ValueError(_not_available(_CONVERSION, name).current)

    # Answered from the static metadata, so the backend is never imported for a file it would reject.
    if pipeline != AUTO and (supported := registration.extensions) is not None:
        extension = match_file_extension(filename, supported)

        if extension and extension not in supported:
            raise ValueError(
                _unsupported_extension(
                    name, extension, ", ".join(sorted(supported))
                ).current
            )

    converter = registration.build(settings.conversion.pipelines.get(name, {}), host)

    return _recovering(converter, filename) if pipeline == AUTO else converter


def plain_text() -> PlainText:
    """The `PLAIN_TEXT` converter with the operator's options, for projecting text in process."""
    return configure(PlainText, settings.conversion.pipelines.get(PLAIN_TEXT, {}), HOST)


@cache
def chunker_fingerprint(pipeline: str) -> str:
    """The fingerprint of the chunker *pipeline* names with the operator's options, see `exar.ingest.fingerprint`.

    It changes with the options, not with the code of exar, so an update that
    chunks otherwise ships a migration clearing the stamps of the documents.
    """
    return fingerprint(get_chunker(pipeline))


def get_chunker(pipeline: str) -> AsyncChunker:
    """Build the chunker *pipeline* names, AUTO resolving to `DEFAULT_CHUNKING`.

    Raises:
        ValueError: If the pipeline's requirements or condition are not met.
        ValidationError: If the operator's options are invalid for the pipeline.
    """
    name = DEFAULT_CHUNKING if pipeline == AUTO else pipeline
    registration = CHUNKERS[name]

    if not registration.available():
        raise ValueError(_not_available(_CHUNKING, name).current)

    return registration.build(settings.chunking.pipelines.get(name, {}), HOST)


def _check_options(
    stage: str,
    registry: Mapping[str, Pipeline[Any]],
    options: Mapping[str, Mapping[str, Any]],
) -> None:
    for name, values in options.items():
        registration = registry.get(name)

        if registration is None or not registration.available():
            raise ValueError(
                f"{stage}.pipelines.{name} configures a pipeline that is not available"
            )

        # Building imports no backend, so every configured pipeline is checked.
        _ = registration.build(values, HOST)


def check_pipelines() -> None:
    """Validate the operator's pipeline options at boot, so a typo fails loudly.

    Raises:
        ValueError: For options to a pipeline that is unknown, unavailable, or takes none.
        ValidationError: For options a pipeline refuses.
    """
    _check_options("conversion", CONVERTERS, settings.conversion.pipelines)
    _check_options("chunking", CHUNKERS, settings.chunking.pipelines)
