"""The structured channel of tool results, as the client receives and the harness reads it.

A tool returns a ``ToolReturn`` whose ``metadata`` is its typed data
(:func:`~hivegent.tools.base.tool_return`), a plain value being a ``ToolReturn`` without it (``exar.tools.batch.unpack``).  The output spill wraps that data in a
:class:`ToolOutputChunk` stamped with the call's id (:func:`with_data_chunk`),
which is what the Vercel stream sends and the conversation stores.
"""

from dataclasses import replace
from typing import Any, override

from exar.tools.batch import as_return
from pydantic import PrivateAttr
from pydantic_ai.messages import ToolReturn
from pydantic_ai.ui.vercel_ai.response_types import DataChunk
from pydantic_core import to_json

__all__ = [
    "DATA_CHUNK_TYPE",
    "ToolOutputChunk",
    "with_data_chunk",
]

DATA_CHUNK_TYPE = "data-tool-output"
"""DataChunk type used to stream structured tool data to the frontend."""


def _encode(data: object) -> bytes:
    """*data* as JSON, the way a streamed chunk spells it."""
    return to_json(data, by_alias=True, exclude_none=True)


class ToolOutputChunk(DataChunk):
    """The structured payload of a tool return, as the frontend receives it.

    The data is encoded once, by :meth:`data_json`, and that encoding is
    what both the output spill measures and saves and the stream sends.
    """

    _json: bytes | None = PrivateAttr(default=None)

    def data_json(self) -> bytes:
        """The data as JSON, encoded on the first call and kept."""
        if self._json is None:
            self._json = _encode(self.data)

        return self._json

    @override
    def encode(self, sdk_version: int) -> str:
        """The streamed chunk, built around the one encoding of its data."""
        head = self.model_dump_json(by_alias=True, exclude_none=True, exclude={"data"})

        return f'{head[:-1]},"data":{self.data_json().decode()}}}'


def with_data_chunk(result: Any, tool_call_id: str | None) -> ToolReturn[Any]:
    """*result* with its typed data wrapped in a :class:`ToolOutputChunk` for the call *tool_call_id*.

    The id is the only reliable link between a chunk and its tool part, since
    the Vercel AI SDK appends ``data-*`` parts to the end of a message.
    Text data is the return value itself and is not repeated, and a chunk
    already built passes as it is.

    >>> with_data_chunk(ToolReturn("text", metadata={"a": 1}), "call").metadata.id
    'call'
    """
    returned = as_return(result)

    if returned.metadata is None or isinstance(returned.metadata, DataChunk):
        return returned

    if isinstance(returned.metadata, str):
        return replace(returned, metadata=None)

    chunk = ToolOutputChunk(
        type=DATA_CHUNK_TYPE, id=tool_call_id, data=returned.metadata
    )

    return replace(returned, metadata=chunk)
