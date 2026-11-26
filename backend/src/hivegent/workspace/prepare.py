"""Lock-free per-kind preparation of an upload.

The slow work of an upload (conversion, vision captioning, frame sampling)
runs here without the workspace lock and without touching the live workspace,
producing the side-effect-free :class:`PreparedUpload` that the changeset
gateway (:func:`~hivegent.workspace.changeset.apply_changeset`) then lands.
"""

import logging
import tempfile
from collections.abc import Generator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from urllib.parse import urlsplit

import logfire
from exar.helpers import bounded_gather
from exar.ingest import (
    Asset,
    AsyncParser,
    Parsed,
    drop_assets,
    informative,
    perceptual_key,
    relink,
)
from exar.ingest import contexts as occurrences
from exar.text import decode
from pydantic import ValidationError

from ..config import sanitize_document_path, settings
from ..llm_config import background_llm
from ..media import guess_image_media_type
from ..metadata import EntryGeneratedBy, EntryKind, EntryMetadata, EntryOrigin
from ..pipelines import (
    AUTO,
    PLAIN_TEXT,
    get_converter,
    plain_text,
    resolve_auto_pipeline,
)
from ..shared.formats import EntryProjection, media_type, projection_for
from ..shared.l10n import Localized
from ..sources.catalog import conversion_of
from ..store import Workspace
from ..tools.changes import ChangeRefused
from ..tools.entries import Part, asset_ref_for, stem_path_from_reference
from ..types import ProgressReporter
from .describe import _build_image_description, _build_video_description
from .metadata import _build_entry_metadata
from .paths import not_text

__all__ = ["PreparedUpload", "prepare_upload"]

logger = logging.getLogger(__name__)


_STAGE_IMAGE = Localized(
    en="Generating image description", de="Bildbeschreibung wird erstellt"
)
_STAGE_VIDEO = Localized(
    en="Generating video description", de="Videobeschreibung wird erstellt"
)
_STAGE_PROCESSING = Localized(en="Processing document", de="Dokument wird verarbeitet")
_STAGE_DESCRIBING = Localized(en="Describing images", de="Bilder werden beschrieben")


# URL schemes whose references resolve to a fetchable resource, any other
# (`file:`, a Windows drive letter like `T:`, ...) points off the workspace.
_SERVABLE_SCHEMES = frozenset({"http", "https", "data"})


def _is_external_ref(ref: str) -> bool:
    """Return whether *ref* points outside the workspace and cannot be served.

    Converters sometimes leak the author's unreachable image location into the
    markdown: an absolute path, a Windows drive path, a `file:` URI, or a
    backslash path.  Web and inline (`data:`) sources are valid and kept.

    >>> _is_external_ref("media/image1.png"), _is_external_ref(r"T:\\grafik\\logo.jpg")
    (False, True)
    >>> _is_external_ref("/var/folders/tmp/media/image65.jpg"), _is_external_ref("https://example.com/a.png")
    (True, False)
    """
    ref = ref.strip()

    if not ref:
        return False

    if scheme := urlsplit(ref).scheme:
        return scheme not in _SERVABLE_SCHEMES

    return ref.startswith("/") or "\\" in ref


def _conversion_failed(reason: str) -> Localized[str]:
    return Localized(
        en=f"Conversion failed: {reason}", de=f"Konvertierung fehlgeschlagen: {reason}"
    )


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
class PreparedUpload:
    """The side-effect-free result of preparing an upload.

    Produced lock-free, since the slow work (conversion, vision captioning,
    frame sampling) happens here, then written to the workspace under the
    workspace lock and indexed without it by the changeset gateway.  Holding
    the lock only for the file writes, not the whole pipeline, is what keeps
    the rest of the workspace usable while a long upload runs.
    """

    main: _PreparedEntry
    assets: tuple[_PreparedAsset, ...] = ()
    asset_entries: tuple[_PreparedEntry, ...] = ()


@contextmanager
def _source_on_disk(filepath: str, content: bytes) -> Generator[Path]:
    """Materialise upload bytes at a temp path for converters that read a file.

    Keeps the original basename so format detection by suffix still works, and
    lives outside the workspace, so a lock-free conversion never touches the live
    entry.  The commit is the only step that writes into the workspace.
    """
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / PurePosixPath(filepath).name
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
    converted document).  User markdown is the exception.  Its own file is the
    description, so it does not use this.
    """
    stem_path = stem_path_from_reference(filepath)
    description_path = Part(stem_path, "description").path
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
    *,
    origin: EntryOrigin,
) -> _PreparedEntry:
    """Build the caption entry for an image without touching disk or SQL.

    Shared by standalone image uploads and the described assets extracted from
    a converted document; *contexts* carries every occurrence's surrounding
    text so the caption is the single source of truth for that image.
    """
    markdown = await _build_image_description(filepath, content, media_type, contexts)
    return _derived_entry(
        filepath, markdown, entry_kind="image", generated_by="vision", origin=origin
    )


def _relink_assets(markdown: str, mapping: Mapping[str, str | None]) -> str:
    """Point the image references of *markdown* at the stored assets *mapping* names, dropping those it maps to ``None``.

    References left unmapped are dropped when they point outside the
    workspace (absolute, ``file:``, or Windows paths a converter could not
    localize) and kept otherwise.
    """

    def link(path: str) -> str | None:
        if path in mapping:
            return mapping[path]

        return None if _is_external_ref(path) else path

    return relink(markdown, link)


def _prepare_markdown(
    store: Workspace,
    filepath: str,
    content: bytes,
    *,
    origin: EntryOrigin,
    original_path: str | None,
) -> PreparedUpload:
    """Prepare a user-authored markdown document for commit.

    It has no assets: the commit clears the ``.assets`` directory of an entry
    it replaces, and a new entry's stem holds none.
    """
    decoded = decode(content)
    if decoded is None:
        raise ChangeRefused(
            status_code=422, detail=not_text(store.scope.render(filepath)).current
        )
    stem_path = stem_path_from_reference(filepath)
    main = _PreparedEntry(
        description_path=filepath,
        markdown=decoded.text,
        entry_metadata=_build_entry_metadata(
            stem_path=stem_path,
            description_path=filepath,
            original_path=original_path,
            assets_dir=None,
            entry_kind="user_markdown",
            origin=origin,
            generated_by="user",
        ),
    )
    return PreparedUpload(main=main)


async def _prepare_image(
    filepath: str,
    content: bytes,
    *,
    origin: EntryOrigin,
    ctx: ProgressReporter | None,
) -> PreparedUpload:
    """Prepare a standalone image and its generated description for commit."""
    if ctx is not None:
        ctx.set_stage(_STAGE_IMAGE.current)

    media_type = guess_image_media_type(filepath) or ""
    entry = await _prepare_image_entry(
        filepath,
        content,
        media_type,
        [f"File name: {PurePosixPath(filepath).name}"],
        origin=origin,
    )
    return PreparedUpload(main=entry)


async def _prepare_video(
    filepath: str,
    content: bytes,
    *,
    origin: EntryOrigin,
    ctx: ProgressReporter | None,
) -> PreparedUpload:
    """Prepare a video and its frame-based description for commit.

    The original is the entry's payload and the vision-generated markdown is
    its searchable projection.  Frames are sampled with OpenCV from a temp copy
    of the source (see :func:`~hivegent.media.sample_video`), so the
    lock-free prepare never touches the live workspace entry.
    """
    if ctx is not None:
        ctx.set_stage(_STAGE_VIDEO.current)

    with _source_on_disk(filepath, content) as full_path:
        markdown = await _build_video_description(
            filepath,
            full_path,
            [f"File name: {PurePosixPath(filepath).name}"],
        )
    main = _derived_entry(
        filepath, markdown, entry_kind="video", generated_by="vision", origin=origin
    )
    return PreparedUpload(main=main)


def _prepare_plain_text_or_stub(
    filepath: str, content: bytes, *, origin: EntryOrigin
) -> PreparedUpload:
    """Prepare AUTO input as plain text or a metadata-only binary stub.

    Supported Unicode and Western text is prepared as a searchable plain-text
    document, while unsupported or binary-looking bytes get a metadata-only
    stub.
    """
    entry_kind: EntryKind = "convertible"
    generated_by: EntryGeneratedBy = "converter"

    try:
        markdown = (
            plain_text().parse_bytes(content, PurePosixPath(filepath).suffix).markdown
        )
    except ValueError:
        entry_kind, generated_by = "binary_stub", "stub"
        mime = media_type(filepath) or "application/octet-stream"
        markdown = (
            f"File name: {PurePosixPath(filepath).name}.\n"
            f"MIME type: {mime}.\nSize: {len(content)} bytes.\n"
        )

    main = _derived_entry(
        filepath,
        markdown,
        entry_kind=entry_kind,
        generated_by=generated_by,
        origin=origin,
    )
    return PreparedUpload(main=main)


async def _prepare_conversion_assets(
    assets_dir: str,
    images: Mapping[str, Asset],
    contexts_by_ref: dict[str, list[str]],
    *,
    describe: bool,
) -> tuple[dict[str, str | None], list[_PreparedAsset], list[_PreparedEntry]]:
    """Triage, deduplicate, and caption a conversion's extracted images.

    Lock-free: returns the markdown reference remapping plus the asset files
    and caption entries to apply at commit time, without touching disk or SQL.
    Undescribed assets (decorative, or all without *describe*) keep their own reference;
    described assets are grouped by :func:`perceptual_key` so an image is
    captioned once and every occurrence's reference is rewritten to the single
    stored representative, never once per occurrence.
    """

    def child_path(relpath: str) -> str:
        # The converter names the file, so `..` or an absolute name is refused.
        return str(
            (PurePosixPath(assets_dir) / sanitize_document_path(relpath)).as_posix()
        )

    ref_mapping: dict[str, str | None] = {}
    assets: list[_PreparedAsset] = []
    # Group described occurrences by perceptual identity so duplicates collapse
    # to one captioned entry. Images with no stable key (uniform or undecodable)
    # use their unique relative path so each stays its own singleton group.
    groups: dict[int | str, list[str]] = {}

    for relpath, extracted in sorted(images.items()):
        if not (describe and informative(extracted)):
            ref_mapping[relpath] = asset_ref_for(assets_dir, relpath)
            assets.append(_PreparedAsset(child_path(relpath), extracted.data))
            continue

        key = perceptual_key(extracted.data)
        groups.setdefault(key if key is not None else relpath, []).append(relpath)

    for members in groups.values():
        rep_ref = asset_ref_for(assets_dir, members[0])
        for member in members:
            ref_mapping[member] = rep_ref

    async def _caption_group(members: list[str]) -> _PreparedEntry:
        representative = members[0]
        rep_path = child_path(representative)
        media_type = guess_image_media_type(representative) or ""
        contexts: list[str] = []
        for member in members:
            contexts.extend(contexts_by_ref.get(member, []))
            if caption := images[member].caption:
                contexts.append(f"Figure caption: {caption}")
        assets.append(_PreparedAsset(rep_path, images[representative].data))
        return await _prepare_image_entry(
            rep_path,
            images[representative].data,
            media_type,
            contexts,
            origin="extracted",
        )

    # Caption unique images concurrently, but capped: a figure-heavy document can
    # have dozens of distinct images, and one unbounded vision request per image
    # trips provider rate limits, so retries make it slower rather than faster.
    asset_entries = await bounded_gather(
        groups.values(), _caption_group, limit=settings.llm.caption_concurrency
    )
    return ref_mapping, assets, asset_entries


def _build_converter(pipeline: str, basename: str, *, describe: bool) -> AsyncParser:
    """Instantiate the converter for a resolved pipeline, mapping its errors to HTTP."""
    try:
        return get_converter(
            pipeline,
            filename=basename,
            classify_assets=describe,
        )
    except ValidationError:
        # Operator options the converter refuses fail loudly, never as a fallback.
        raise
    except ValueError as exc:
        # Only an explicit pipeline can name one that is uninstalled or wrong for the file.
        raise ChangeRefused(status_code=400, detail=str(exc)) from exc


async def _convert_source(
    filepath: str, content: bytes, converter: AsyncParser, pipeline: str
) -> Parsed:
    """Convert a temp copy of the source, raising the converter's exception."""
    with (
        logfire.span("convert_document", filepath=filepath, pipeline=pipeline) as span,
        _source_on_disk(filepath, content) as source_path,
    ):
        result = await converter(source_path)
        span.set_attribute("markdown_length", len(result.markdown))
        span.set_attribute("image_count", len(result.assets))

        if result.encoding is not None:
            span.set_attribute("source_encoding", result.encoding)

        return result


async def _prepare_convertible(
    filepath: str,
    content: bytes,
    conversion_pipeline: str,
    *,
    origin: EntryOrigin,
    ctx: ProgressReporter | None,
) -> PreparedUpload:
    """Convert a binary and prepare its markdown plus extracted assets.

    Runs the converter against a temp copy of the source, then prepares the
    asset files and caption entries, all without the workspace lock and without
    touching the live workspace entry, so a long conversion never blocks the
    rest of the workspace and a failure mid-conversion leaves nothing behind.
    """
    if ctx is not None:
        ctx.set_stage(_STAGE_PROCESSING.current)

    basename = PurePosixPath(filepath).name
    is_auto = conversion_pipeline == AUTO
    if is_auto and resolve_auto_pipeline(basename) == PLAIN_TEXT:
        # Skip the converter's temp-file round trip: AUTO already falls back
        # to the same projection (or a stub) for anything it cannot decode.
        return _prepare_plain_text_or_stub(filepath, content, origin=origin)

    # Describing the extracted assets is background work, which `llm.aux` opts into.
    describe = background_llm() is not None
    converter = _build_converter(conversion_pipeline, basename, describe=describe)

    try:
        result = await _convert_source(
            filepath, content, converter, conversion_pipeline
        )
    except Exception as exc:
        if not is_auto:
            raise ChangeRefused(
                status_code=500, detail=_conversion_failed(str(exc)).current
            ) from exc

        prepared = _prepare_plain_text_or_stub(filepath, content, origin=origin)

        # Text the converter refused is still indexed, so only a stub is worth
        # a warning.
        # exc_info captures the chained cause: docling re-raises pipeline
        # errors as a bare "Pipeline ... failed" with the root cause only
        # attached via `from`.
        if prepared.main.entry_metadata.entry_kind == "binary_stub":
            logger.warning(
                "AUTO conversion failed for %s, indexing as stub: %s",
                filepath,
                exc,
                exc_info=exc,
            )
        else:
            logger.info(
                "%s could not convert %s, indexed as plain text: %s",
                conversion_pipeline,
                filepath,
                exc,
            )

        return prepared

    assets_dir = Part(stem_path_from_reference(filepath), "assets").path
    markdown = result.markdown

    assets: tuple[_PreparedAsset, ...] = ()
    asset_entries: tuple[_PreparedEntry, ...] = ()

    # A converter without exar's `extract_assets` fact may still extract or reference them.
    if not settings.conversion.extract_assets:
        markdown = drop_assets(result).markdown
        has_assets = False
    else:
        if ctx is not None and describe and result.assets:
            ctx.set_stage(_STAGE_DESCRIBING.current)

        ref_mapping, asset_list, entry_list = await _prepare_conversion_assets(
            assets_dir,
            result.assets,
            occurrences(markdown),
            describe=describe,
        )
        markdown = _relink_assets(markdown, ref_mapping)
        has_assets = bool(result.assets)
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
    return PreparedUpload(main=main, assets=assets, asset_entries=asset_entries)


async def prepare_upload(
    store: Workspace,
    filepath: str,
    content: bytes,
    *,
    origin: EntryOrigin,
    original_path: str | None,
    ctx: ProgressReporter | None,
) -> PreparedUpload:
    """Dispatch to the per-kind preparation. No lock held.

    *original_path* is a markdown upload's companion original, ``None`` for
    none, while every other kind is its own original.
    """
    match projection_for(filepath):
        case EntryProjection.MARKDOWN:
            return _prepare_markdown(
                store, filepath, content, origin=origin, original_path=original_path
            )
        case EntryProjection.IMAGE:
            return await _prepare_image(filepath, content, origin=origin, ctx=ctx)
        case EntryProjection.VIDEO:
            return await _prepare_video(filepath, content, origin=origin, ctx=ctx)
        case EntryProjection.CONVERTIBLE:
            return await _prepare_convertible(
                filepath, content, conversion_of(store), origin=origin, ctx=ctx
            )
