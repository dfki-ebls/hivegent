"""Shared helpers for the document routes' background jobs.

Uploads, collections, rechunk, and reconvert all run as background jobs
(see :mod:`hivegent.jobs`). This module holds the upload size guard those
routes share and the per-file bulk runner that reports its progress to a job
context.
"""

import logging
from collections.abc import Awaitable, Callable, Sequence
from pathlib import Path

from exar.helpers import bounded_as_completed
from fastapi import HTTPException, UploadFile

from ...config import settings
from ...files import transient
from ...shared.l10n import Localized
from ...types import FailedFile, ProgressReporter

__all__ = [
    "enforce_upload_size",
    "run_bulk_document_job",
    "spool_dir",
    "summarize_failed_files",
    "summarize_failures",
    "upload_body_limit",
]

logger = logging.getLogger(__name__)

# Cap how many failed filenames a job-failure message lists, so a batch that
# fails wholesale does not produce an unwieldy error string.
_MAX_LISTED_FAILURES = 20


def _more_failures(count: int) -> Localized[str]:
    return Localized(en=f", and {count} more", de=f" und {count} weitere")


_REASON_SEPARATOR = Localized(en="; ", de=". ")


def _bulk_failed(
    verb: str, succeeded: int, total: int, failed: int, names: str
) -> Localized[str]:
    return Localized(
        en=f"{verb} {succeeded} of {total}; {failed} failed: {names}",
        de=f"{verb}: {succeeded} von {total}. {failed} fehlgeschlagen: {names}",
    )


def summarize_failures(
    failed: Sequence[str], *, limit: int = _MAX_LISTED_FAILURES
) -> str:
    """Join failed filenames for a job-failure message, capping a long list."""
    listed = ", ".join(failed[:limit])
    if len(failed) > limit:
        listed += _more_failures(len(failed) - limit).current
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

    return _REASON_SEPARATOR.current.join(
        f"{reason}: {summarize_failures(paths, limit=limit)}"
        for reason, paths in by_reason.items()
    )


def spool_dir() -> Path:
    """Directory holding upload payloads spooled to disk for background jobs, emptied at boot with the transient folder."""
    path = transient("spool")
    path.mkdir(parents=True, exist_ok=True)
    return path


async def run_bulk_document_job(
    files: list[str],
    process_one: Callable[[str, ProgressReporter], Awaitable[None]],
    *,
    verb: Localized[str],
    ctx: ProgressReporter,
) -> None:
    """Run a per-file mutation over documents, reporting progress to *ctx*.

    Each ``process_one`` call is a workspace mutation that maintains its own
    index entries and reports its stages to *ctx*, so this helper only drives
    the batch, up to ``collection_concurrency`` files at once. A failed file
    (including an unauthorized path, whose access check raises) is logged and
    the batch keeps going. If any file failed, the job is raised as failed with
    the count and names, so a partial or total failure surfaces in the tray
    instead of settling as a misleading success.
    """
    total = len(files)
    done = 0
    failed: list[str] = []
    ctx.set_progress(0, total)

    async def run(filepath: str) -> tuple[str, Exception | None]:
        try:
            await process_one(filepath, ctx)
        except Exception as exc:  # noqa: BLE001
            return filepath, exc

        return filepath, None

    async with bounded_as_completed(
        files, run, limit=settings.jobs.collection_concurrency
    ) as results:
        async for filepath, error in results:
            if error is not None:
                logger.warning(
                    "Bulk %s failed for %s: %s", verb.en.lower(), filepath, error
                )
                failed.append(filepath)

            done += 1
            ctx.set_progress(done, total)

    if failed:
        raise RuntimeError(
            _bulk_failed(
                verb.current,
                total - len(failed),
                total,
                len(failed),
                summarize_failures(failed),
            ).current
        )


_FORM_OVERHEAD = 1024 * 1024
"""What a multipart body holds beside its file, the form fields and the encoding."""


def upload_body_limit(limit: int) -> int:
    """The largest body of an upload whose file may take *limit* bytes.

    FastAPI refuses a larger body with a 413 while it arrives, before any of it
    is spooled, and :func:`enforce_upload_size` holds the file to *limit* with
    the localized reason.
    """
    return limit + _FORM_OVERHEAD


def enforce_upload_size(
    file: UploadFile, *, limit: int, too_large: Callable[[int], Localized[str]]
) -> None:
    """Reject an upload whose size exceeds *limit*.

    Starlette populates ``UploadFile.size`` while parsing the multipart body and
    has already spooled the bytes to ``file.file`` by the time the route runs,
    so the cap is enforced by inspecting the received upload rather than
    re-reading it.  ``size`` is ``None`` only for uploads not built by the
    multipart parser, which are left uncapped.
    """
    if file.size is not None and file.size > limit:
        raise HTTPException(status_code=413, detail=too_large(limit).current)
