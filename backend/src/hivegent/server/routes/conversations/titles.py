"""The route generating the title of a conversation."""

import logging
from collections.abc import Sequence

from exar.tools.files import BLOCK_SEP
from fastapi import APIRouter, HTTPException
from pydantic_ai.messages import ModelMessage, TextPart, UserPromptPart
from starlette.requests import Request

from ....agents import title_agent
from ....db.conversations import (
    UNTITLED,
    load_conversation_data,
    set_conversation_title,
    title_line,
)
from ....llm import model_from_config, thinking_model_settings
from ....llm_config import aux_llm
from ....shared.l10n import Localized
from ....types import GenerateTitleResponse
from ...cancellation import run_until_disconnect
from ...common import found
from ...rate_limit import GENERATION_LIMIT
from .deps import Owned

__all__ = ["router"]

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


def _extract_message_texts(
    messages: Sequence[ModelMessage],
    max_messages: int = 4,
) -> list[str]:
    """Extract text content from conversation messages."""
    texts: list[str] = []
    for message in messages:
        for part in message.parts:
            if (
                isinstance(part, (UserPromptPart, TextPart))
                and isinstance(part.content, str)
                and (text := part.content.strip())
            ):
                texts.append(text[:500])
                if len(texts) >= max_messages:
                    return texts
    return texts


@router.post(
    "/conversations/{conversation_id}/title/generation",
    dependencies=[GENERATION_LIMIT],
)
async def generate_conversation_title(
    conversation: Owned,
    http_request: Request,
) -> GenerateTitleResponse:
    """Generate a title for a conversation with the aux model."""
    data = found(await load_conversation_data(conversation))
    aux = aux_llm()
    if aux is None:
        raise HTTPException(status_code=503, detail=_NO_TITLE_MODEL.current)

    messages_text = _extract_message_texts(data.messages)
    if not messages_text:
        return GenerateTitleResponse(title=data.title or UNTITLED.current)

    conversation_preview = BLOCK_SEP.join(messages_text)

    async def _generate() -> str:
        result = await title_agent.run(
            _title_prompt(conversation_preview).current,
            model=model_from_config(aux),
            model_settings=thinking_model_settings(aux),
            instructions=_TITLE_INSTRUCTIONS.current,
        )

        return result.output

    try:
        output = await run_until_disconnect(http_request, _generate())
        generated_title = title_line(output.strip().strip("\"'"))
        # A title the user set while this one was generated wins, and the
        # summary reads no title as an empty one.
        summary = found(
            await set_conversation_title(
                conversation, generated_title, expected_title=data.title or None
            )
        )

        return GenerateTitleResponse(title=summary.title)
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception(
            "Failed to generate title for conversation %s", conversation.id
        )
        raise HTTPException(status_code=500, detail=_TITLE_FAILED.current) from exc
