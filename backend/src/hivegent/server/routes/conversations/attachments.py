"""The images a chat message carries, checked against what the model accepts."""

import asyncio
from collections.abc import Iterable, Iterator, Sequence

from exar.ingest import WorkerCrashed, downscale, sanitize_image
from exar.text import plural
from fastapi import HTTPException
from pydantic_ai import BinaryContent
from pydantic_ai.messages import (
    FilePart,
    ModelMessage,
    ModelRequest,
    ToolReturnPart,
    UserContent,
    UserPromptPart,
)

from ....agents.guards import cut_text
from ....config import settings
from ....db.conversations import prompt_items, prompt_text
from ....llm_config import LlmConfig, run_max_images
from ....media import ISOLATED
from ....shared.formats import INGESTIBLE_IMAGE_MEDIA_TYPES
from ....shared.l10n import Localized, format_bytes

__all__ = ["accept_attachments", "accept_imported"]

_NO_VISION = Localized(
    en="The selected model does not accept images",
    de="Das gewählte Modell nimmt keine Bilder an",
)


_ONLY_IMAGES = Localized(
    en=(
        "Only images can be attached to a chat message. Upload other "
        "documents to your workspace, where they are converted and "
        "indexed for the assistant to search."
    ),
    de=(
        "An eine Chatnachricht können nur Bilder angehängt werden. Lade andere "
        "Dokumente in deinen Arbeitsbereich hoch, wo sie konvertiert und für die "
        "Suche durch den Assistenten indexiert werden."
    ),
)


_UNREADABLE = Localized(
    en="The attached image could not be read.",
    de="Das angehängte Bild konnte nicht gelesen werden.",
)


def _image_too_large(limit: int) -> Localized[str]:
    return Localized(
        en=f"Attached image exceeds the {format_bytes(limit)} limit.",
        de=f"Das angehängte Bild überschreitet die Grenze von {format_bytes(limit)}.",
    )


def _too_many_images(cap: int) -> Localized[str]:
    return Localized(
        en=(
            f"At most {cap} {plural(cap, 'image')} can be attached to a message, "
            "since the model server accepts no more in one request."
        ),
        de=(
            f"An eine Nachricht {plural(cap, 'kann', 'können')} höchstens {cap} "
            f"{plural(cap, 'Bild', 'Bilder')} angehängt werden, da der "
            "Modellserver nicht mehr pro Anfrage annimmt."
        ),
    )


def _attached_items(messages: Sequence[ModelMessage]) -> Iterator[UserContent]:
    """The non-text items of every user prompt in *messages*, in order."""
    for message in messages:
        if not isinstance(message, ModelRequest):
            continue

        for part in message.parts:
            if isinstance(part, UserPromptPart):
                yield from (i for i in prompt_items(part) if prompt_text(i) is None)


async def _accept_attachment(item: UserContent) -> None:
    """Admit one attached item, sanitising it in place, or reject it.

    Raises :class:`HTTPException` for anything but an image within the
    size cap, so no URL is ever fetched for a prompt.  A still image larger
    than a vision model reads is downscaled in an isolated worker
    (:func:`exar.ingest.downscale`), any other PNG stripped of the ancillary
    chunks that trip Pillow-based inference servers, the same best-effort
    passes ``read_binary_document`` applies to an image read off disk, and
    the provider metadata a client may send along is dropped.
    """
    if (
        not isinstance(item, BinaryContent)
        or item.media_type not in INGESTIBLE_IMAGE_MEDIA_TYPES
    ):
        raise HTTPException(
            status_code=400,
            detail=_ONLY_IMAGES.current,
        )

    limit = settings.limits.max_attachment_bytes
    if len(item.data) > limit:
        raise HTTPException(
            status_code=400,
            detail=_image_too_large(limit).current,
        )

    try:
        shrunk = await asyncio.get_running_loop().run_in_executor(
            ISOLATED, downscale, item.data
        )
    except (ValueError, WorkerCrashed, TimeoutError) as exc:
        raise HTTPException(status_code=400, detail=_UNREADABLE.current) from exc

    item.data, item.media_type = shrunk or (
        sanitize_image(item.data, item.media_type),
        item.media_type,
    )
    item.vendor_metadata = None


async def accept_attachments(
    messages: Sequence[ModelMessage], *, llm: LlmConfig
) -> None:
    """Admit the attachments of ``ChatAdapter.messages`` for *llm*, rejecting non-images.

    Takes the client's new prompt alone, never the replayed prefix, so an
    already-admitted image is never re-scanned on a later turn.  Anything
    but an image belongs in a workspace instead (see ``AGENTS.md``), a
    model without vision takes none at all, and none takes more than its
    image cap.

    Check the count before sanitizing any images in an over-cap request.
    """
    cap = run_max_images(llm)
    items = list(_attached_items(messages))

    if items and not llm.vision:
        raise HTTPException(status_code=400, detail=_NO_VISION.current)

    if cap is not None and len(items) > cap:
        raise HTTPException(
            status_code=400,
            detail=_too_many_images(cap).current,
        )

    await _accept_all(items)


def _cut(part: ToolReturnPart, max_chars: int) -> None:
    """Cut the result of *part* to *max_chars*, as a run cuts one."""
    if len(text := part.model_response_str()) > max_chars:
        part.content = cut_text(text, max_chars)


async def _accept_all(items: Iterable[UserContent]) -> None:
    """Admit every item at once, the rest cancelled with the first rejection."""
    try:
        async with asyncio.TaskGroup() as group:
            for item in items:
                _ = group.create_task(_accept_attachment(item))
    except* HTTPException as rejected:
        raise rejected.exceptions[0] from None


async def accept_imported(messages: Sequence[ModelMessage]) -> None:
    """Admit the attachments of an imported history as :func:`accept_attachments` does a new prompt's.

    The model's own caps are left to the turns that replay it, since an
    archive names no model, but no message carries more images than the
    gateway's cap, every attachment must be an inline image within the size
    cap, and the files of its replies carry no client metadata either.  A
    tool result is cut to ``llm.tool_output_max_chars``, as a run cuts one.
    """
    cap = run_max_images(None)

    if cap is not None and any(
        len(list(_attached_items([message]))) > cap for message in messages
    ):
        raise HTTPException(status_code=400, detail=_too_many_images(cap).current)

    await _accept_all(_attached_items(messages))
    max_chars = settings.llm.tool_output_max_chars

    for message in messages:
        for part in message.parts:
            if isinstance(part, FilePart):
                part.content.vendor_metadata = None

            elif isinstance(part, ToolReturnPart):
                _cut(part, max_chars)
