"""Shared helpers for the document routes' background jobs.

Single uploads, reconvert, collections, and the bulk operations all run as
background jobs (see :mod:`hivegent.server.jobs`); this module holds the
upload size guard those routes share, collection-request validation, and the
per-file bulk runner that reports its progress to a job context.
"""

import logging
import shutil
from collections.abc import Awaitable, Callable, Sequence
from pathlib import Path

from fastapi import HTTPException, UploadFile

from ...config import settings
from ...humanize import format_bytes
from ...types import FailedFile, LlmConfig, PipelineSpec, ProgressReporter
from ..common import parse_pipeline_spec, prepare_llm_config

__all__ = [
    "cleanup_spool_dir",
    "enforce_upload_size",
    "run_bulk_document_job",
    "spool_dir",
    "summarize_failed_files",
    "summarize_failures",
    "validate_collection_upload",
]

logger = logging.getLogger(__name__)

# Cap how many failed filenames a job-failure message lists, so a batch that
# fails wholesale does not produce an unwieldy error string.
_MAX_LISTED_FAILURES = 20


def summarize_failures(
    failed: Sequence[str], *, limit: int = _MAX_LISTED_FAILURES
) -> str:
    """Join failed filenames for a job-failure message, capping a long list."""
    listed = ", ".join(failed[:limit])
    if len(failed) > limit:
        listed += f", and {len(failed) - limit} more"
    return listed


def summarize_failed_files(
    failed: Sequence[FailedFile], *, limit: int = _MAX_LISTED_FAILURES
) -> str:
    """Group failed files by reason into one compact clause per reason.

    Files sharing a reason are listed together (``reason: a, b``) so a batch
    that fails the same way stays one short line instead of repeating the
    reason per file.
    """
    by_reason: dict[str, list[str]] = {}
    for f in failed:
        by_reason.setdefault(f.reason, []).append(f.path)

    return "; ".join(
        f"{reason}: {summarize_failures(paths, limit=limit)}"
        for reason, paths in by_reason.items()
    )


def _spool_root() -> Path:
    """The single source of truth for where job spool files live."""
    return settings.data_dir / "spool"


def spool_dir() -> Path:
    """Directory holding upload payloads spooled to disk for background jobs."""
    path = _spool_root()
    path.mkdir(parents=True, exist_ok=True)
    return path


def cleanup_spool_dir() -> None:
    """Drop spool files orphaned by a restart, called once at startup.

    Jobs live only in memory, so any spool file present at startup belongs to a
    job a restart cut short — its ``on_settled`` unlink never ran — and is safe
    to remove.
    """
    path = _spool_root()
    if path.exists():
        shutil.rmtree(path, ignore_errors=True)


async def run_bulk_document_job(
    files: list[str],
    process_one: Callable[[str], Awaitable[None]],
    *,
    verb: str,
    ctx: ProgressReporter,
) -> None:
    """Run a per-file mutation over many documents, reporting progress to *ctx*.

    Each ``process_one`` call is a workspace mutation that maintains its own
    index entries; this helper only drives the loop. A failed file (including an
    unauthorized path, whose access check raises) is logged and the batch keeps
    going, so the files that can be processed are. If any file failed, the job is
    raised as failed with the count and names, so a partial or total failure
    surfaces in the tray instead of settling as a misleading success.
    """
    total = len(files)
    failed: list[str] = []
    ctx.set_progress(0, total)

    for index, filepath in enumerate(files):
        try:
            await process_one(filepath)
        except Exception as exc:  # noqa: BLE001
            # One file's failure must not abort the batch; it is collected and
            # reported as a partial result once every file has been attempted.
            logger.warning("Bulk %s failed for %s: %s", verb.lower(), filepath, exc)
            failed.append(filepath)

        ctx.set_progress(index + 1, total)

    if failed:
        succeeded = total - len(failed)
        raise RuntimeError(
            f"{verb} {succeeded} of {total}; "
            f"{len(failed)} failed: {summarize_failures(failed)}"
        )


async def validate_collection_upload(
    pipeline_spec: str,
    llm_config: str,
) -> tuple[PipelineSpec, LlmConfig]:
    """Parse and validate pipeline and LLM configuration for collection uploads.

    Async because the SSRF check runs DNS off the event loop.
    """
    spec = parse_pipeline_spec(pipeline_spec)
    resolved = await prepare_llm_config(LlmConfig.model_validate_json(llm_config))
    return spec, resolved


def enforce_upload_size(file: UploadFile, *, limit: int, label: str) -> None:
    """Reject an upload whose size exceeds *limit*.

    Starlette populates ``UploadFile.size`` while parsing the multipart body and
    has already spooled the bytes to ``file.file`` by the time the route runs,
    so the cap is enforced by inspecting the received upload rather than
    re-reading it.  ``size`` is ``None`` only for uploads not built by the
    multipart parser, which are left uncapped.
    """
    if file.size is not None and file.size > limit:
        raise HTTPException(
            status_code=413,
            detail=f"{label} too large. Maximum size: {format_bytes(limit)}",
        )
