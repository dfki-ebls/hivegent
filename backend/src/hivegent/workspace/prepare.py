"""Lock-free per-kind preparation of an upload.

The slow work of an upload — conversion, vision captioning, frame sampling —
runs here without the casebase lock and without touching the live workspace,
producing the side-effect-free :class:`_PreparedUpload` that
:func:`hivegent.workspace.commit._apply_prepared` then lands.
"""

import asyncio
import logging
import mimetypes
import re
import tempfile
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

import logfire
from fastapi import HTTPException
from pydantic import ValidationError

from ..chunkers.base import EntryGeneratedBy, EntryKind, EntryMetadata, EntryOrigin
from ..config import settings
from ..converters import (
    ConversionPipeline,
    EntryProjection,
    get_converter,
    projection_for,
    resolve_auto_pipeline,
)
from ..converters.asset_processing import (
    MD_IMAGE_RE,
    TriageDecision,
    image_context_windows,
    perceptual_key,
    triage_image,
)
from ..converters.base import (
    ConversionResult,
    DocumentConverter,
    ExtractedImage,
    is_external_ref,
)
from ..converters.fallbacks import recover_conversion
from ..converters.images import guess_image_media_type
from ..converters.plain_text import convert_plain_text
from ..entries import (
    asset_ref_for,
    assets_dir_for_stem,
    description_path_for_stem,
    stem_path_from_reference,
)
from ..llm_config import LlmConfig
from ..store import Casebase
from ..text import NOT_TEXT_REASON, decode_bytes
from ..types import AssetProcessingMode, PipelineSpec, ProgressReporter
from .describe import _build_image_description, _build_video_description
from .metadata import _build_entry_metadata

__all__: list[str] = []

logger = logging.getLogger(__name__)


def _encoding_note(source_encoding: str | None) -> str:
    """Render the message suffix reporting a transcode, empty when there was none.

    CP1252 is a fallback for undeclared Western input, so every upload whose
    bytes were not already UTF-8 reports the source encoding in its response.
    """
    return f" (decoded from {source_encoding})" if source_encoding else ""


@dataclass(slots=True, frozen=True)
class _PreparedEntry:
    """A markdown projection to write and index when an upload commits."""

    description_path: str
    markdown: str
    entry_metadata: EntryMetadata


@dataclass(slots=True, frozen=True)
class _PreparedAsset:
    """An extracted asset file to write verbatim when an upload commits."""

    path: str
    data: bytes


@dataclass(slots=True, frozen=True)
class _PreparedUpload:
    """The side-effect-free result of preparing an upload.

    Produced lock-free — the slow work (conversion, vision captioning,
    frame sampling) happens here — then written to the workspace under the
    casebase lock and indexed without it by :func:`_apply_prepared`.
    Holding the lock only for the file writes, not the whole pipeline, is
    what keeps the rest of the workspace usable while a long upload runs.
    """

    main: _PreparedEntry
    filename: str
    size_bytes: int
    message: str
    converted_filename: str | None = None
    conversion_pipeline_used: str | None = None
    assets: tuple[_PreparedAsset, ...] = ()
    asset_entries: tuple[_PreparedEntry, ...] = ()


@dataclass(slots=True, frozen=True)
class _Reserved:
    """What an upload's locked reserve phase captured for prepare and commit.

    Reserve only validates and reads — it never mutates the workspace — so a
    failure during the lock-free prepare leaves any pre-existing entry intact.
    These fields tell :func:`_write_prepared_files` how to apply the new content
    and supersede a prior entry, all under the lock.  ``original_path`` is also
    recorded in the entry metadata, while ``original_content`` is present only
    when commit must write it.  ``preserve`` marks a reprocess of an existing
    entry whose old paths are restored if apply fails.  A fresh upload is rolled
    back by deletion if indexing fails.
    """

    reference: str
    content: bytes
    origin: EntryOrigin
    original_path: str | None = None
    original_content: bytes | None = None
    preserve: bool = False
    supersede_original: str | None = None


@contextmanager
def _source_on_disk(filepath: str, content: bytes) -> Iterator[Path]:
    """Materialise upload bytes at a temp path for converters that read a file.

    Keeps the original basename so format detection by suffix still works, and
    lives outside the workspace so a lock-free conversion never touches the live
    entry — the commit is the only step that writes into the workspace.
    """
    with tempfile.TemporaryDirectory(prefix="hivegent-convert-") as tmp_dir:
        path = Path(tmp_dir) / PurePosixPath(filepath).name
        path.write_bytes(content)
        yield path


def _derived_entry(
    filepath: str,
    markdown: str,
    *,
    entry_kind: EntryKind,
    generated_by: EntryGeneratedBy,
    origin: EntryOrigin,
    assets_dir: str | None = None,
) -> _PreparedEntry:
    """Build a prepared entry whose original is *filepath* and *markdown* its projection.

    Centralises the stem → description derivation shared by every upload kind
    that keeps a separate original (image, video, binary stub, plain text,
    converted document).  User markdown is the exception — its own file is the
    description — so it does not use this.
    """
    stem_path = stem_path_from_reference(filepath)
    description_path = description_path_for_stem(stem_path)
    return _PreparedEntry(
        description_path=description_path,
        markdown=markdown,
        entry_metadata=_build_entry_metadata(
            stem_path=stem_path,
            description_path=description_path,
            original_path=filepath,
            assets_dir=assets_dir,
            entry_kind=entry_kind,
            origin=origin,
            generated_by=generated_by,
        ),
    )


async def _prepare_image_entry(
    filepath: str,
    content: bytes,
    media_type: str,
    contexts: Sequence[str],
    llm: LlmConfig,
    *,
    origin: EntryOrigin,
) -> _PreparedEntry:
    """Build the caption entry for an image without touching disk or SQL.

    Shared by standalone image uploads and the described assets extracted from
    a converted document; *contexts* carries every occurrence's surrounding
    text so the caption is the single source of truth for that image.
    """
    markdown = await _build_image_description(
        filepath, content, media_type, contexts, llm
    )
    return _derived_entry(
        filepath, markdown, entry_kind="image", generated_by="vision", origin=origin
    )


def _replace_image_references(markdown: str, mapping: dict[str, str | None]) -> str:
    """Rewrite or strip ``![alt](path)`` references in *markdown*.

    Bounded to real markdown image syntax so prose that mentions an
    asset's filename (code blocks, file listings) is left untouched.
    Mapping values: a string replaces the URL; ``None`` deletes the
    image node entirely. References left unmapped are dropped when they
    point outside the workspace (absolute, ``file:``, or Windows paths a
    converter could not localize) and kept otherwise.
    """

    def _replace(match: re.Match[str]) -> str:
        path = match.group(2)
        if path in mapping:
            target = mapping[path]
            return "" if target is None else f"![{match.group(1)}]({target})"
        return "" if is_external_ref(path) else match.group(0)

    return MD_IMAGE_RE.sub(_replace, markdown)


def _prepare_markdown(
    store: Casebase,
    filepath: str,
    content: bytes,
    *,
    origin: EntryOrigin,
    original_path: str | None,
    clearing_assets: bool,
) -> _PreparedUpload:
    """Prepare a user-authored markdown document for commit.

    A markdown entry surfaces a companion ``.assets`` directory if one exists,
    except when the commit will clear it (an overwrite), so the metadata never
    claims a directory the same commit is about to remove.
    """
    workspace_dir = store.workspace_dir(settings.data_dir)
    decoded = decode_bytes(content)
    if decoded is None:
        raise HTTPException(
            status_code=422,
            detail=f"'{filepath}' {NOT_TEXT_REASON}",
        )
    text = decoded.text
    stem_path = stem_path_from_reference(filepath)
    assets_dir = assets_dir_for_stem(stem_path)
    has_assets = not clearing_assets and (workspace_dir / assets_dir).exists()
    main = _PreparedEntry(
        description_path=filepath,
        markdown=text,
        entry_metadata=_build_entry_metadata(
            stem_path=stem_path,
            description_path=filepath,
            original_path=original_path,
            assets_dir=assets_dir if has_assets else None,
            entry_kind="user_markdown",
            origin=origin,
            generated_by="user",
        ),
    )
    return _PreparedUpload(
        main=main,
        filename=filepath,
        size_bytes=len(content),
        message=(
            f"Document uploaded successfully{_encoding_note(decoded.source_encoding)}"
        ),
    )


async def _prepare_image(
    filepath: str,
    content: bytes,
    llm: LlmConfig,
    *,
    origin: EntryOrigin,
    ctx: ProgressReporter | None,
) -> _PreparedUpload:
    """Prepare a standalone image and its generated description for commit."""
    if ctx is not None:
        ctx.set_stage("Generating image description")

    media_type = guess_image_media_type(filepath) or ""
    entry = await _prepare_image_entry(
        filepath,
        content,
        media_type,
        [f"File name: {PurePosixPath(filepath).name}"],
        llm,
        origin=origin,
    )
    return _PreparedUpload(
        main=entry,
        filename=filepath,
        size_bytes=len(content),
        converted_filename=entry.description_path,
        message="Image uploaded and described successfully",
    )


async def _prepare_video(
    filepath: str,
    content: bytes,
    llm: LlmConfig,
    *,
    origin: EntryOrigin,
    ctx: ProgressReporter | None,
) -> _PreparedUpload:
    """Prepare a video and its frame-based description for commit.

    The original is the entry's payload and the vision-generated markdown is
    its searchable projection.  Frames are sampled via ffmpeg from a temp copy
    of the source (see :func:`~hivegent.converters.video.sample_video`), so the
    lock-free prepare never touches the live workspace entry.
    """
    if ctx is not None:
        ctx.set_stage("Generating video description")

    with _source_on_disk(filepath, content) as full_path:
        markdown = await _build_video_description(
            filepath,
            full_path,
            [f"File name: {PurePosixPath(filepath).name}"],
            llm,
        )
    main = _derived_entry(
        filepath, markdown, entry_kind="video", generated_by="vision", origin=origin
    )
    return _PreparedUpload(
        main=main,
        filename=filepath,
        size_bytes=len(content),
        converted_filename=main.description_path,
        message="Video uploaded and described successfully",
    )


def _prepare_plain_text_or_stub(
    filepath: str, content: bytes, *, origin: EntryOrigin
) -> _PreparedUpload:
    """Prepare AUTO input as plain text or a metadata-only binary stub.

    Supported Unicode and Western text is prepared as a searchable plain-text
    document, while unsupported or binary-looking bytes get a metadata-only
    stub.
    """
    result = convert_plain_text(content, PurePosixPath(filepath).suffix)
    entry_kind: EntryKind = "binary_stub" if result is None else "convertible"
    generated_by: EntryGeneratedBy = "stub" if result is None else "converter"

    if result is None:
        mime = mimetypes.guess_type(filepath)[0] or "application/octet-stream"
        markdown = (
            f"File name: {PurePosixPath(filepath).name}.\n"
            f"MIME type: {mime}.\nSize: {len(content)} bytes.\n"
        )
        pipeline_used = None
        message = "Binary file uploaded with searchable stub"
    else:
        markdown = result.markdown
        pipeline_used = ConversionPipeline.PLAIN_TEXT.value
        message = (
            f"Document uploaded as plain text{_encoding_note(result.source_encoding)}"
        )

    main = _derived_entry(
        filepath,
        markdown,
        entry_kind=entry_kind,
        generated_by=generated_by,
        origin=origin,
    )
    return _PreparedUpload(
        main=main,
        filename=filepath,
        size_bytes=len(content),
        converted_filename=main.description_path,
        conversion_pipeline_used=pipeline_used,
        message=message,
    )


async def _prepare_conversion_assets(
    assets_dir: str,
    images: dict[str, ExtractedImage],
    contexts_by_ref: dict[str, list[str]],
    mode: AssetProcessingMode,
    llm: LlmConfig,
) -> tuple[dict[str, str | None], list[_PreparedAsset], list[_PreparedEntry]]:
    """Triage, deduplicate, and caption a conversion's extracted images.

    Lock-free: returns the markdown reference remapping plus the asset files
    and caption entries to apply at commit time, without touching disk or SQL.
    Store-only assets (decorative, or ``STORE`` mode) keep their own reference;
    described assets are grouped by :func:`perceptual_key` so an image is
    captioned once and every occurrence's reference is rewritten to the single
    stored representative — never once per occurrence.
    """

    def child_path(relpath: str) -> str:
        return str((PurePosixPath(assets_dir) / relpath).as_posix())

    ref_mapping: dict[str, str | None] = {}
    assets: list[_PreparedAsset] = []
    # Group described occurrences by perceptual identity so duplicates collapse
    # to one captioned entry. Images with no stable key (uniform or undecodable)
    # use their unique relative path so each stays its own singleton group.
    groups: dict[int | str, list[str]] = {}

    for relpath, extracted in sorted(images.items()):
        describe_image = (
            mode is AssetProcessingMode.DESCRIBE
            and triage_image(extracted) is TriageDecision.DESCRIBE
        )
        if not describe_image:
            ref_mapping[relpath] = asset_ref_for(assets_dir, relpath)
            assets.append(_PreparedAsset(child_path(relpath), extracted.data))
            continue

        key = perceptual_key(extracted.data)
        groups.setdefault(key if key is not None else relpath, []).append(relpath)

    for members in groups.values():
        rep_ref = asset_ref_for(assets_dir, members[0])
        for member in members:
            ref_mapping[member] = rep_ref

    asset_entries: list[_PreparedEntry] = []
    # Caption unique images concurrently, but capped: a figure-heavy document can
    # have dozens of distinct images, and one unbounded vision request per image
    # trips provider rate limits, so retries make it slower rather than faster.
    caption_slots = asyncio.Semaphore(settings.llm.caption_concurrency)

    async def _caption_group(members: list[str]) -> None:
        representative = members[0]
        rep_path = child_path(representative)
        media_type = guess_image_media_type(representative) or ""
        contexts: list[str] = []
        for member in members:
            contexts.extend(contexts_by_ref.get(member, []))
            if caption := images[member].caption:
                contexts.append(f"Figure caption: {caption}")
        assets.append(_PreparedAsset(rep_path, images[representative].data))
        async with caption_slots:
            entry = await _prepare_image_entry(
                rep_path,
                images[representative].data,
                media_type,
                contexts,
                llm,
                origin="extracted",
            )
        asset_entries.append(entry)

    await asyncio.gather(*(_caption_group(members) for members in groups.values()))
    return ref_mapping, assets, asset_entries


def _build_converter(
    pipeline: ConversionPipeline, basename: str, spec: PipelineSpec, llm: LlmConfig
) -> DocumentConverter:
    """Instantiate the converter for a resolved pipeline, mapping its errors to HTTP."""
    try:
        return get_converter(
            pipeline,
            filename=basename,
            config=spec.conversion.config,
            llm_options=llm,
            detect_asset_roles=spec.process_assets is AssetProcessingMode.DESCRIBE,
        )
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=exc.errors()) from exc
    except (ImportError, ValueError) as exc:
        # AUTO reaches this point only for an available richer converter whose
        # declared extensions produced the mapping.  Only an explicit pipeline
        # can name one that is uninstalled or wrong for the file.
        raise HTTPException(status_code=400, detail=str(exc)) from exc


async def _convert_source(
    filepath: str,
    content: bytes,
    converter: DocumentConverter,
    pipeline: ConversionPipeline,
    *,
    allow_recovery: bool,
) -> tuple[ConversionResult, ConversionPipeline]:
    """Convert a temp copy of the source, returning the result and the pipeline used.

    *allow_recovery* (AUTO only) lets a failed or degraded primary conversion be
    replaced by a fallback converter's text; an explicit pipeline keeps its own
    output and surfaces its own error.  Raises the converter's exception when
    nothing recovered it.
    """
    suffix = PurePosixPath(filepath).suffix.lower()
    with (
        logfire.span(
            "convert_document",
            filepath=filepath,
            converter=converter.name,
            pipeline=pipeline.value,
        ) as span,
        _source_on_disk(filepath, content) as source_path,
    ):
        outcome: ConversionResult | Exception
        try:
            outcome = await converter(source_path)
        except Exception as exc:  # noqa: BLE001
            outcome = exc

        recovery = (
            await recover_conversion(source_path, suffix, outcome)
            if allow_recovery
            else None
        )
        if recovery is not None:
            failure = outcome if isinstance(outcome, Exception) else None
            # A recovered result is text only (fallbacks drop images), so asset
            # processing never captions figures the markdown lost.
            logger.warning(
                "primary conversion of %s %s; recovered via %s fallback",
                filepath,
                "failed" if failure is not None else "produced degraded output",
                recovery.pipeline.value,
                exc_info=failure,
            )
            result = ConversionResult(markdown=recovery.markdown)
            pipeline = recovery.pipeline
        elif isinstance(outcome, Exception):
            raise outcome
        else:
            result = outcome

        span.set_attribute("markdown_length", len(result.markdown))
        span.set_attribute("image_count", len(result.images))
        if result.source_encoding is not None:
            span.set_attribute("source_encoding", result.source_encoding)

        return result, pipeline


async def _prepare_convertible(
    filepath: str,
    content: bytes,
    spec: PipelineSpec,
    llm: LlmConfig,
    *,
    origin: EntryOrigin,
    ctx: ProgressReporter | None,
) -> _PreparedUpload:
    """Convert a binary and prepare its markdown plus extracted assets.

    Runs the converter against a temp copy of the source, then prepares the
    asset files and caption entries — all without the casebase lock and without
    touching the live workspace entry, so a long conversion never blocks the
    rest of the workspace and a failure mid-conversion leaves nothing behind.
    """
    if ctx is not None:
        ctx.set_stage("Processing document")

    basename = PurePosixPath(filepath).name
    conversion_pipeline = spec.conversion.pipeline
    is_auto = conversion_pipeline == ConversionPipeline.AUTO
    if is_auto and resolve_auto_pipeline(basename) is ConversionPipeline.PLAIN_TEXT:
        # Skip the converter's temp-file round trip: AUTO already falls back
        # to the same projection (or a stub) for anything it cannot decode.
        return _prepare_plain_text_or_stub(filepath, content, origin=origin)

    converter = _build_converter(conversion_pipeline, basename, spec, llm)
    resolved_conversion = ConversionPipeline(converter.name)

    try:
        result, resolved_conversion = await _convert_source(
            filepath, content, converter, resolved_conversion, allow_recovery=is_auto
        )
    except Exception as exc:
        if is_auto:
            # exc_info captures the chained cause: docling re-raises pipeline
            # errors as a bare "Pipeline ... failed" with the root cause only
            # attached via `from`.
            logger.warning(
                "AUTO conversion failed for %s, indexing as plain text or stub: %s",
                filepath,
                exc,
                exc_info=exc,
            )
            return _prepare_plain_text_or_stub(filepath, content, origin=origin)
        raise HTTPException(
            status_code=500,
            detail=f"Conversion failed: {exc!s}",
        ) from exc

    assets_dir = assets_dir_for_stem(stem_path_from_reference(filepath))
    markdown = result.markdown

    mode = spec.process_assets
    assets: tuple[_PreparedAsset, ...] = ()
    asset_entries: tuple[_PreparedEntry, ...] = ()
    if mode is AssetProcessingMode.IGNORE:
        markdown = _replace_image_references(
            markdown, {ref: None for ref in result.images}
        )
        has_assets = False
    else:
        if ctx is not None and mode is AssetProcessingMode.DESCRIBE and result.images:
            ctx.set_stage("Describing images")
        ref_mapping, asset_list, entry_list = await _prepare_conversion_assets(
            assets_dir,
            result.images,
            image_context_windows(markdown),
            mode,
            llm,
        )
        markdown = _replace_image_references(markdown, ref_mapping)
        has_assets = bool(result.images)
        assets = tuple(asset_list)
        asset_entries = tuple(entry_list)

    main = _derived_entry(
        filepath,
        markdown,
        entry_kind="convertible",
        generated_by="converter",
        origin=origin,
        assets_dir=assets_dir if has_assets else None,
    )
    return _PreparedUpload(
        main=main,
        filename=filepath,
        size_bytes=len(content),
        converted_filename=main.description_path,
        conversion_pipeline_used=resolved_conversion.value,
        assets=assets,
        asset_entries=asset_entries,
        message=(
            "Document uploaded and converted successfully"
            f"{_encoding_note(result.source_encoding)}"
        ),
    )


async def _prepare_upload(
    store: Casebase,
    filepath: str,
    content: bytes,
    spec: PipelineSpec,
    llm: LlmConfig,
    *,
    origin: EntryOrigin,
    original_path: str | None,
    ctx: ProgressReporter | None,
    clearing_assets: bool,
) -> _PreparedUpload:
    """Dispatch to the per-kind preparation. No lock held."""
    match projection_for(filepath):
        case EntryProjection.MARKDOWN:
            return _prepare_markdown(
                store,
                filepath,
                content,
                origin=origin,
                original_path=original_path,
                clearing_assets=clearing_assets,
            )
        case EntryProjection.IMAGE:
            return await _prepare_image(filepath, content, llm, origin=origin, ctx=ctx)
        case EntryProjection.VIDEO:
            return await _prepare_video(filepath, content, llm, origin=origin, ctx=ctx)
        case EntryProjection.CONVERTIBLE:
            return await _prepare_convertible(
                filepath, content, spec, llm, origin=origin, ctx=ctx
            )
