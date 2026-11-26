"""Compacting a conversation's history on request, and saving a turn's messages."""

import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass

from exar.agents import session
from exar.helpers import shield_to_completion
from exar.pydantic_ai.compaction import Compacted, compaction_cut
from fastapi import HTTPException
from pydantic_ai.conversation import Conversation as RunConversation
from pydantic_ai.messages import ModelMessage, ModelResponse
from pydantic_ai.ui import NativeEvent
from pydantic_ai.ui.vercel_ai.response_types import BaseChunk
from pydantic_ai.usage import RunUsage

from ....agents import RunPrefix
from ....agents.compaction import PrefixSummary
from ....db.conversations import OwnedConversation, store
from ....shared.l10n import Localized
from ....tmp import open_tmp
from ...turns import display_order, stamp, ui_id
from ...vercel import turn_snapshot
from .deps import Adapter

__all__ = ["NOTHING_TO_COMPACT", "SavedTurn", "compact_history", "save_turn"]

logger = logging.getLogger(__name__)


NOTHING_TO_COMPACT = Localized(
    en="There is nothing to compact: the conversation has no answered turn since its last compaction, or a request still awaits an answer.",
    de="Es gibt nichts zu komprimieren: Seit der letzten Komprimierung hat die Konversation keine beantwortete Runde, oder eine Anfrage wartet noch auf eine Antwort.",
)


@dataclass(slots=True)
class SavedTurn:
    """The turn a client swaps in and the history stored with it, ``None`` until it is stored."""

    snapshot: BaseChunk | None = None
    messages: list[ModelMessage] | None = None


async def save_turn(
    conversation: OwnedConversation,
    loaded: RunConversation,
    messages: list[ModelMessage],
    saved: SavedTurn,
) -> None:
    """Save *messages* as the history of *loaded*, and keep the turn the client swaps in.

    The store appends what extends the stored history and branches what
    rewrites it.  The turn starts at the first message the save stored, in
    the order it shows, so the client replaces what it streamed from there on,
    and the snapshot is projected from what was saved rather than read back,
    from there on only.
    """
    loaded.messages = messages
    new = {
        id(entry.message)
        for entry in await store.save(loaded, scope=conversation.owner)
    }
    first = next(
        (
            messages[index]
            for index in display_order(messages)
            if id(messages[index]) in new
        ),
        None,
    )
    saved.messages = messages
    saved.snapshot = turn_snapshot(
        messages,
        loaded.deferred_tool_requests,
        None if first is None else ui_id(first),
    )


def compact_history(
    conversation: OwnedConversation,
    adapter: Adapter,
    run_prefix: RunPrefix,
    loaded: RunConversation,
    history: list[ModelMessage],
    reply_id: str,
    saved: SavedTurn,
) -> AsyncIterator[BaseChunk]:
    """Stream the compaction of *history*, saved at its cut once made.

    Asked for under the same prefix a turn would run under, so the provider
    answers it from its cache.  The adapter frames it as the event a run's own
    compaction streams as, so the client renders it as it renders the stored
    one on reload, and an error reaches it as a run error would.  It shows in
    the reply *reply_id*, the last one, or a new one after a failed prompt,
    which it is stored ahead of, on a new branch.
    """
    cut = compaction_cut(history)

    if cut is None:
        raise HTTPException(status_code=400, detail=NOTHING_TO_COMPACT.current)

    open_tmp(conversation)

    async def events() -> AsyncIterator[NativeEvent]:
        try:
            usage = RunUsage()

            # The root of an exar run like a turn, whose policy the gate holds the declared tools to.
            async with session(
                "", run_prefix.deps.run_config, name="compact", record=False
            ):
                part = await PrefixSummary(prefix=run_prefix).compact(
                    history[:cut], usage
                )

            loaded.usage = loaded.usage + usage
            compaction = ModelResponse(parts=[part])
            stamp([compaction], reply_id)
            # Run to completion like a turn's write, so a cancel during it
            # neither loses a summary already paid for nor outlives the turn.
            await shield_to_completion(
                save_turn(
                    conversation,
                    loaded,
                    [*history[:cut], compaction, *history[cut:]],
                    saved,
                )
            )

        except Exception:
            logger.exception("Failed to compact conversation %s", conversation.id)
            raise

        yield Compacted(part=part)

    return adapter.transform_stream(events())
