"""Public upload, replace, and reconvert operations.

Each is one :class:`~hivegent.workspace.operations.Upload` committed through
the changeset gateway, which converts it without a lock and lands it
atomically, so a failed or cancelled conversion never destroys what was
already there.  Replacing an original and reconverting one are uploads over
the entry, of new bytes or of its own.
"""

import asyncio
from pathlib import PurePosixPath

from ..changes import Basis
from ..chunkers.base import EntryOrigin
from ..entries import ContentStat, stem_path_from_reference
from ..llm_config import LlmConfig
from ..store import Casebase
from ..types import PipelineSpec, ProgressReporter
from .changeset import apply_changeset
from .metadata import find_original, resolve_entry
from .operations import Location, Upload

__all__ = [
    "reconvert",
    "replace_original",
    "upload",
]


async def upload(
    store: Casebase,
    filepath: str,
    content: bytes,
    *,
    spec: PipelineSpec | None = None,
    llm: LlmConfig | None = None,
    origin: EntryOrigin = "upload",
    companion: tuple[str, bytes] | None = None,
    overwrite: bool = False,
    basis: Basis | None = None,
    ctx: ProgressReporter | None = None,
) -> None:
    """Upload *content* to *filepath* in *store*, converting and indexing it.

    Args:
        store: The workspace to upload to.
        filepath: The file, local to *store*.
        content: Its bytes.
        spec: The conversion and chunking pipelines.
        llm: The model that describes images and videos.
        origin: How a new entry came to be.
        companion: A markdown upload's original, a sibling path with its bytes.
        overwrite: Whether it replaces the entry at the same stem, see
            :class:`~hivegent.workspace.operations.Upload`.
        basis: What the caller saw of *filepath*, if anything.
        ctx: Where the conversion reports its stages.
    """
    item = Upload(
        Location(store, filepath),
        content,
        spec=spec or PipelineSpec(),
        llm=llm or LlmConfig(),
        origin=origin,
        companion=companion,
        overwrite=overwrite,
        basis=basis,
        progress=ctx,
    )
    await apply_changeset((item,))


async def replace_original(
    store: Casebase,
    safe: str,
    content: bytes,
    *,
    new_filename: str | None,
    spec: PipelineSpec | None = None,
    llm: LlmConfig | None = None,
    ctx: ProgressReporter | None = None,
) -> None:
    """Replace the original behind *safe*'s entry with *content*, converted afresh.

    The new original keeps the entry's stem and takes the suffix of
    *new_filename*, else the one the original had, and an entry without one
    gains it.
    """
    current = (await resolve_entry(store, safe)).original_path
    suffix = PurePosixPath(new_filename or current or "").suffix
    target = f"{stem_path_from_reference(safe)}{suffix.lower()}"
    await upload(store, target, content, spec=spec, llm=llm, overwrite=True, ctx=ctx)


async def reconvert(
    store: Casebase,
    safe: str,
    *,
    spec: PipelineSpec | None = None,
    llm: LlmConfig | None = None,
    ctx: ProgressReporter | None = None,
) -> None:
    """Convert the original behind *safe*'s entry again, an upload of its own bytes over it.

    Raises:
        HTTPException: When the entry has no original.
    """
    located = await find_original(store, safe)

    def read() -> tuple[ContentStat | None, bytes]:
        # The stat comes first, so a change while reading refuses the commit.
        return ContentStat.from_path(located), located.read_bytes()

    basis, content = await asyncio.to_thread(read)
    original = located.relative_to(store.path.resolve()).as_posix()
    await upload(
        store,
        original,
        content,
        spec=spec,
        llm=llm,
        overwrite=True,
        basis=basis,
        ctx=ctx,
    )
