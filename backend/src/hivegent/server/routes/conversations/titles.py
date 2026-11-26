"""The titles the aux model writes, on demand and after a new conversation's first turn."""

import logging
from collections.abc import Iterable, Iterator, Sequence
from itertools import islice

from exar.tools.files import BLOCK_SEP
from fastapi import APIRouter, HTTPException
from pydantic_ai.messages import ModelMessage, TextPart
from starlette.requests import Request

from ....agents import title_agent
from ....db.conversations import (
    UNTITLED,
    OwnedConversation,
    is_prompt,
    load_conversation_data,
    prompt_items,
    prompt_text,
    set_conversation_title,
    title_line,
)
from ....llm import model_from_config, thinking_model_settings
from ....llm_config import LlmConfig, aux_llm
from ....shared.l10n import Localized
from ....types import GenerateTitleResponse
from ...cancellation import run_until_disconnect
from ...common import found
from ...rate_limit import GENERATION_LIMIT
from .compaction import SavedTurn
from .deps import Owned

__all__ = ["router", "title_first_turn"]

logger = logging.getLogger(__name__)
router = APIRouter()


_NO_TITLE_MODEL = Localized(
    en="No model is configured to generate titles",
    de="Es ist kein Modell zum Erzeugen von Titeln konfiguriert",
)


_TITLE_INSTRUCTIONS = Localized(
    en="""Generate a short, descriptive title (max 60 characters) for the conversation.
The title should capture the main topic or question.
Write the title in the language of the conversation, or in English if that is unclear.
Return ONLY the title, no quotes or extra text.
""",
    de="""Erstelle einen kurzen, aussagekräftigen Titel (maximal 60 Zeichen) für die Konversation.
Der Titel soll das Hauptthema oder die Hauptfrage erfassen.
Schreibe den Titel in der Sprache der Konversation oder auf Deutsch, wenn diese unklar ist.
Gib NUR den Titel zurück, ohne Anführungszeichen oder zusätzlichen Text.
""",
)


def _title_prompt(preview: str) -> Localized[str]:
    return Localized(en=f"Conversation:\n{preview}", de=f"Konversation:\n{preview}")


_TITLE_FAILED = Localized(
    en="Failed to generate title", de="Titel konnte nicht erstellt werden"
)


def _texts(messages: Sequence[ModelMessage]) -> Iterator[str]:
    """The texts of the user's prompts and of the answers in order, each clipped, attachments and blanks skipped."""
    for message in messages:
        for part in message.parts:
            texts: Iterable[str | None]

            if is_prompt(part):
                texts = map(prompt_text, prompt_items(part))
            elif isinstance(part, TextPart):
                texts = (part.content,)
            else:
                continue

            for text in texts:
                if text and (text := text.strip()):
                    yield text[:500]


async def _generate_title(
    messages: Sequence[ModelMessage], llm: LlmConfig
) -> str | None:
    """A title for the first four texts of *messages* written by *llm*, ``None`` when they hold none."""
    texts = list(islice(_texts(messages), 4))

    if not texts:
        return None

    result = await title_agent.run(
        _title_prompt(BLOCK_SEP.join(texts)).current,
        model=model_from_config(llm),
        model_settings=thinking_model_settings(llm),
        instructions=_TITLE_INSTRUCTIONS.current,
    )

    return title_line(result.output.strip().strip("\"'"))


async def title_first_turn(
    conversation: OwnedConversation,
    saved: SavedTurn,
    llm: LlmConfig,
    claimed: str | None,
) -> None:
    """Title a new conversation with *llm* from its first turn, its prompt and answer, once *saved* holds it stored.

    Never over a title the user set in place of the *claimed* one, ``None``
    for a conversation claimed untitled.
    """
    if saved.messages is None:
        return

    if (title := await _generate_title(saved.messages, llm)) is not None:
        await set_conversation_title(conversation, title, expected_title=claimed)


@router.post(
    "/conversations/{conversation_id}/title/generation",
    dependencies=[GENERATION_LIMIT],
)
async def generate_conversation_title(
    conversation: Owned,
    http_request: Request,
) -> GenerateTitleResponse:
    """Generate a title for a conversation with the aux model."""
    aux = aux_llm()

    if aux is None:
        raise HTTPException(status_code=503, detail=_NO_TITLE_MODEL.current)

    data = found(await load_conversation_data(conversation))

    try:
        title = await run_until_disconnect(
            http_request, _generate_title(data.messages, aux)
        )
    except Exception as exc:
        logger.exception(
            "Failed to generate title for conversation %s", conversation.id
        )
        raise HTTPException(status_code=500, detail=_TITLE_FAILED.current) from exc

    if title is None:
        return GenerateTitleResponse(title=data.title or UNTITLED.current)

    # A title the user set while this one was generated wins, and the
    # summary reads no title as an empty one.
    summary = found(
        await set_conversation_title(
            conversation, title, expected_title=data.title or None
        )
    )

    return GenerateTitleResponse(title=summary.title)
