"""Vercel AI chat adapter for the server-owned conversation tree.

The live stream of a turn is in :mod:`~hivegent.server.vercel.adapter`, and the
transcript of a stored history that a reload reads and every turn ends on is in
:mod:`~hivegent.server.vercel.transcript`.
"""

from .adapter import (
    APPROVAL_ARGS_KEY,
    APPROVAL_METADATA_KEY,
    REASONING_DURATIONS_KEY,
    SDK_VERSION,
    ChatAdapter,
    PersistTurn,
    RunEnd,
    StaleAnswers,
    agent_events,
    denial_message,
    run_and_persist,
)
from .transcript import (
    CLIENT_DATA_TYPES,
    TURN_CHUNK_TYPE,
    UI_MESSAGES,
    dump_transcript,
    load_archived,
    stream_response,
    turn_snapshot,
)

__all__ = [
    "APPROVAL_ARGS_KEY",
    "APPROVAL_METADATA_KEY",
    "CLIENT_DATA_TYPES",
    "REASONING_DURATIONS_KEY",
    "SDK_VERSION",
    "TURN_CHUNK_TYPE",
    "UI_MESSAGES",
    "ChatAdapter",
    "PersistTurn",
    "RunEnd",
    "StaleAnswers",
    "agent_events",
    "denial_message",
    "dump_transcript",
    "load_archived",
    "run_and_persist",
    "stream_response",
    "turn_snapshot",
]
