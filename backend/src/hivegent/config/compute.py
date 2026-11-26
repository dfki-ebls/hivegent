"""Settings of the worker pools and of the document processing pipelines."""

from typing import Any, Literal

from pydantic import Field, PositiveFloat, PositiveInt

from .common import Section

__all__ = [
    "ChunkingSettings",
    "ComputeSettings",
    "ConversionSettings",
    "IsolationSettings",
    "JobSettings",
    "OcrSettings",
]


class ComputeSettings(Section):
    """Framework-agnostic compute tunables shared by every neural backend.

    Shared by every backend that runs torch/onnxruntime models (dense
    embeddings, the model-based chunkers, and the docling converter).  Each backend maps these onto its own library and ignores
    what it cannot honor. CPU-only backends (kreuzberg, pdf_oxide)
    disregard ``batch_size`` entirely.

    ``device`` places the models of docling, the local embedding model, and
    the local reranker, the CPU by default, since torch is the CPU build on
    Linux.

    ``num_threads`` caps the CPU-side intra-op / OpenMP threads; keep it
    well under the core count, as Tesseract's OpenMP scales sublinearly.
    ``batch_size`` is the page batch fed to the layout/table/OCR models;
    larger batches trade memory for throughput.

    ``worker_processes`` sizes the persistent worker-process pool (see
    `hivegent.pipelines.POOL`) that converts and chunks documents, one per
    worker at a time, each worker holding its own models.  It is a steady pool
    size, separate from and additive to the single-use isolation workers
    (:attr:`IsolationSettings.max_workers`).  Memory scales with the count, and
    ``num_threads`` is the *total* thread budget that :attr:`threads_per_worker`
    splits across the pool, so keep ``worker_processes`` near the core count with
    ``num_threads`` at or above it.
    ``task_timeout_seconds`` is the wall-clock limit of one conversion or chunking task
    before its worker is killed and replaced, so a hanging converter never pins a worker,
    and ``worker_max_tasks`` the tasks a worker runs before it is replaced against leaks,
    each ``None`` for unlimited.
    """

    num_threads: PositiveInt = 8
    batch_size: PositiveInt = 8
    worker_processes: int = Field(default=1, ge=1)
    task_timeout_seconds: PositiveFloat | None = 1800.0
    worker_max_tasks: PositiveInt | None = 200
    device: Literal["cpu", "mps"] = "cpu"

    @property
    def threads_per_worker(self) -> int:
        """Per-worker intra-op thread budget: ``num_threads`` split across the workers.

        Floored at 1.  The docling and pdf-oxide accelerators and
        the server's own embedding and reranking models read this to size their
        torch/onnxruntime pools, so processes never oversubscribe the cores.
        """
        return max(1, self.num_threads // self.worker_processes)


class OcrSettings(Section):
    """Framework-agnostic OCR tunables shared by every OCR-capable backend.

    ``languages`` lists the Tesseract language codes (ISO 639-2, e.g.
    ``deu``, ``eng``); the matching ``*.traineddata`` packs must resolve
    through ``TESSDATA_PREFIX`` (wired up by the nix package and dev
    shell).  ``skip_native_text`` drops OCR for born-digital PDFs that
    already carry an extractable text layer (classified by pdf-inspector
    inside `exar.ingest.Docling`), while image-only or scanned PDFs fall
    through to OCR unchanged.
    """

    languages: list[str] = ["deu", "eng"]
    skip_native_text: bool = True


class ConversionSettings(Section):
    """Framework-agnostic document conversion tunables.

    Holds the conversion-specific ``ocr`` knobs (languages, native-text
    skip); the threads/batch tunables live in the top-level
    :class:`ComputeSettings`, shared with embeddings and chunkers, as is
    the device.

    ``pipelines`` maps a converter's name to its options, the fields of its
    `exar.ingest` parser such as ``{"docling": {"ocr": false}}``.  The boot
    validates them against each converter's fields.

    ``extract_assets`` has every conversion, uploads and sources alike,
    extract the assets of a document, such as images, and keep them beside
    it, describing the ones the asset triage deems meaningful while
    ``llm.aux`` opts into background work.  Off, the converters skip them,
    and every image reference a converter leaves regardless is dropped.
    """

    ocr: OcrSettings = OcrSettings()
    pipelines: dict[str, dict[str, Any]] = {}
    extract_assets: bool = True


class ChunkingSettings(Section):
    """Operator options per chunking pipeline.

    ``pipelines`` maps a chunker's name to its options, the fields of its
    `exar.ingest` chunker such as ``{"recursive": {"size": 1024}}``.
    The boot validates them against each chunker's fields.
    """

    pipelines: dict[str, dict[str, Any]] = {}


class JobSettings(Section):
    """Background-job manager tunables.

    ``max_concurrency`` caps how many jobs (uploads, collections, bulk ops)
    convert and index at once; ``collection_concurrency`` is the inner cap on how
    many files one collection import processes at once, since a collection is a
    single job that would otherwise walk its files one at a time.  Both stay
    modest because the actual parallelism is bounded by
    :attr:`ComputeSettings.worker_processes` (the conversion/chunking pool) and
    the CPU-bound embedding stage.  ``retain_seconds`` is how long a finished job
    stays visible before it is pruned; ``queue_maxsize`` bounds each subscriber's
    snapshot queue so a stalled SSE consumer cannot leak memory.
    ``max_owner_jobs`` and ``max_owner_spool_bytes`` bound the unfinished upload
    jobs of one user and the bytes they hold spooled on disk, refusing more with 429.
    """

    max_concurrency: PositiveInt = 3
    collection_concurrency: PositiveInt = 4
    retain_seconds: float = 3600.0
    queue_maxsize: int = 1024
    max_owner_jobs: PositiveInt = 20
    max_owner_spool_bytes: PositiveInt = 2 * 1024 * 1024 * 1024  # 2 GiB


class IsolationSettings(Section):
    """Single-use isolation-worker tunables (see `hivegent.media.ISOLATED`).

    These size the single-use, timeout-supervised `exar.ingest.ProcessPool`,
    separate from and additive to the persistent pool
    (:attr:`ComputeSettings.worker_processes`).

    ``max_workers`` caps how many fresh isolation processes run at once across
    the server, a ceiling, not a steady pool size, since a process is started
    per crash-prone native call (pdfium paging, video sampling) and torn down when it
    returns.  ``timeout_seconds`` is the per-call wall-clock limit before the
    worker is killed and the call fails as unreadable input.
    """

    max_workers: int = Field(default=2, ge=1)
    timeout_seconds: float = 60.0
