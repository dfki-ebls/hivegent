"""Lock-free per-kind preparation of an upload.

The slow work of an upload — conversion, vision captioning, frame sampling —
runs here without the casebase lock and without touching the live workspace,
producing the side-effect-free :class:`_PreparedUpload` that
:func:`hivegent.workspace.commit._commit_prepared` then applies atomically.
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

# Module-object import (absolute path) keeps test seams patchable and out of a cycle.
import hivegent.workspace.describe as describe
from ..chunkers.base import EntryGeneratedBy, EntryKind, EntryMetadata, EntryOrigin
from ..config import settings
from ..converters import ConversionPipeline, get_converter, resolve_auto_pipeline
from ..converters.asset_processing import (
    MD_IMAGE_RE,
    TriageDecision,
    image_context_windows,
    perceptual_key,
    triage_image,
)
from ..converters.base import (
    ExtractedImage,
    decode_text,
    is_external_ref,
    is_image_suffix,
    is_markdown_suffix,
)
from ..converters.images import guess_image_media_type
from ..converters.video import is_video_suffix
from ..entries import (
    asset_ref_for,
    assets_dir_for_stem,
    description_path_for_stem,
    stem_path_from_reference,
)
from ..store import Casebase
from ..types import AssetProcessingMode, LlmConfig, PipelineSpec, ProgressReporter
from .metadata import _build_entry_metadata

__all__: list[str] = []

logger = logging.getLogger(__name__)


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
    frame sampling) happens here — then applied to the workspace and SQL
    index atomically under the casebase lock by :func:`_commit_prepared`.
    Holding the lock only for the brief commit, not the whole pipeline, is
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
    These fields tell :func:`_commit_prepared` how to apply the new content and
    supersede a prior entry, all atomically under the lock.  ``preserve`` marks a
    reprocess of an existing entry (reconvert/replace/overwrite): its stale
    assets are cleared at commit and it survives a prepare-phase failure, whereas
    a fresh upload (``preserve=False``) is rolled back by deletion.
    """

    reference: str
    content: bytes
    origin: EntryOrigin
    write_original: bool = False
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
    markdown = await describe._build_image_description(
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
    clearing_assets: bool,
) -> _PreparedUpload:
    """Prepare a user-authored markdown document for commit.

    A markdown entry surfaces a companion ``.assets`` directory if one exists,
    except when the commit will clear it (an overwrite), so the metadata never
    claims a directory the same commit is about to remove.
    """
    workspace_dir = store.workspace_dir(settings.data_dir)
    text = content.decode("utf-8")
    stem_path = stem_path_from_reference(filepath)
    assets_dir = assets_dir_for_stem(stem_path)
    has_assets = not clearing_assets and (workspace_dir / assets_dir).exists()
    main = _PreparedEntry(
        description_path=filepath,
        markdown=text,
        entry_metadata=_build_entry_metadata(
            stem_path=stem_path,
            description_path=filepath,
            original_path=None,
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
        message="Document uploaded successfully",
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
        markdown = await describe._build_video_description(
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


def _prepare_unconvertible(
    filepath: str, content: bytes, *, origin: EntryOrigin
) -> _PreparedUpload:
    """AUTO fallback when no converter fits the file.

    Bytes that decode as UTF-8 are prepared as a plain-text document so their
    content stays searchable; genuinely binary bytes get a metadata-only stub.
    The reserve step has already written the original to the workspace.
    """
    text = decode_text(content)
    if text is None:
        name = PurePosixPath(filepath).name
        mime = mimetypes.guess_type(filepath)[0] or "application/octet-stream"
        stub = f"File name: {name}.\nMIME type: {mime}.\nSize: {len(content)} bytes.\n"
        main = _derived_entry(
            filepath, stub, entry_kind="binary_stub", generated_by="stub", origin=origin
        )
        return _PreparedUpload(
            main=main,
            filename=filepath,
            size_bytes=len(content),
            converted_filename=main.description_path,
            message="Binary file uploaded with searchable stub",
        )

    main = _derived_entry(
        filepath,
        text,
        entry_kind="convertible",
        generated_by="converter",
        origin=origin,
    )
    return _PreparedUpload(
        main=main,
        filename=filepath,
        size_bytes=len(content),
        converted_filename=main.description_path,
        conversion_pipeline_used=ConversionPipeline.TEXT_CHEF.value,
        message="Document uploaded as plain text",
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
    # get a unique sentinel so each stays its own singleton group.
    groups: dict[object, list[str]] = {}

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
        groups.setdefault(key if key is not None else object(), []).append(relpath)

    for members in groups.values():
        rep_ref = asset_ref_for(assets_dir, members[0])
        for member in members:
            ref_mapping[member] = rep_ref

    asset_entries: list[_PreparedEntry] = []

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
        asset_entries.append(
            await _prepare_image_entry(
                rep_path,
                images[representative].data,
                media_type,
                contexts,
                llm,
                origin="extracted",
            )
        )

    await asyncio.gather(*(_caption_group(members) for members in groups.values()))
    return ref_mapping, assets, asset_entries


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

    try:
        converter = get_converter(
            conversion_pipeline,
            filename=basename,
            config=spec.conversion.config,
            llm_options=llm,
            detect_asset_roles=spec.process_assets is AssetProcessingMode.DESCRIBE,
        )
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=exc.errors()) from exc
    except (ImportError, ValueError) as exc:
        if conversion_pipeline == ConversionPipeline.AUTO:
            return _prepare_unconvertible(filepath, content, origin=origin)
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    resolved_conversion = conversion_pipeline
    if conversion_pipeline == ConversionPipeline.AUTO:
        resolved_conversion = resolve_auto_pipeline(basename)

    try:
        with (
            logfire.span(
                "convert_document",
                filepath=filepath,
                converter=converter.name,
                pipeline=resolved_conversion.value,
            ) as span,
            _source_on_disk(filepath, content) as source_path,
        ):
            result = await converter(source_path)
            span.set_attribute("markdown_length", len(result.markdown))
            span.set_attribute("image_count", len(result.images))
    except Exception as exc:
        if conversion_pipeline == ConversionPipeline.AUTO:
            # exc_info captures the chained cause: docling re-raises pipeline
            # errors as a bare "Pipeline ... failed" with the root cause only
            # attached via `from`.
            logger.warning(
                "AUTO conversion failed for %s, indexing as plain text or stub: %s",
                filepath,
                exc,
                exc_info=exc,
            )
            return _prepare_unconvertible(filepath, content, origin=origin)
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
        message="Document uploaded and converted successfully",
    )


async def _prepare_upload(
    store: Casebase,
    filepath: str,
    content: bytes,
    spec: PipelineSpec,
    llm: LlmConfig,
    *,
    origin: EntryOrigin,
    ctx: ProgressReporter | None,
    clearing_assets: bool,
) -> _PreparedUpload:
    """Dispatch to the per-kind preparation. No lock held."""
    suffix = PurePosixPath(filepath).suffix.lower()
    if is_markdown_suffix(suffix):
        return _prepare_markdown(
            store, filepath, content, origin=origin, clearing_assets=clearing_assets
        )
    if is_image_suffix(suffix):
        return await _prepare_image(filepath, content, llm, origin=origin, ctx=ctx)
    if is_video_suffix(suffix):
        return await _prepare_video(filepath, content, llm, origin=origin, ctx=ctx)
    return await _prepare_convertible(
        filepath, content, spec, llm, origin=origin, ctx=ctx
    )
