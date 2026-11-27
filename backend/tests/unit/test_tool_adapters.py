"""How plain tools are served to pydantic-ai: the toolset, the data channel, and argument repair."""

from dataclasses import asdict
from typing import Annotated

import pytest
from exar.pydantic_ai.guards import LenientArguments
from exar.tools import effect
from exar.tools.batch import batch_field, tool_return, unpack
from pydantic_ai import ApprovalRequired, FunctionToolset, RunContext
from pydantic_ai.messages import ToolCallPart, ToolReturn
from pydantic_ai.tool_manager import ToolManager
from pydantic_ai.tools import ToolDefinition
from pydantic_ai.ui.vercel_ai.response_types import BaseChunk

from hivegent.agents.common import AgentTools, Hooked, UserDeps, run_context
from hivegent.agents.guards import HOST_GATE
from hivegent.agents.output import ToolOutputChunk, with_data_chunk
from hivegent.agents.policy import HOST_FLAGS, ToolFlags
from hivegent.store import User
from hivegent.tools.base import ToolFunc
from tests.helpers import in_run


@effect("read")
async def echo(
    queries: Annotated[list[str], batch_field("Queries.")],
) -> ToolReturn[list[str]]:
    """Echo the queries."""
    return tool_return(queries, ",".join(queries))


@effect("write")
async def save(text: str) -> str:
    """Save a text."""
    return text


@effect("read")
async def look(path: str) -> str:
    """Look at an image."""
    return path


def _offer_nothing(
    ctx: RunContext[UserDeps], tool_def: ToolDefinition
) -> ToolDefinition | None:
    return None


def _tools(ctx: RunContext[UserDeps]) -> list[ToolFunc | Hooked]:
    return [echo, save, Hooked(look, prepare=_offer_nothing)]


def _context() -> RunContext[UserDeps]:
    return run_context(UserDeps(user=User(id="u"), mode="interactive"))


def test_the_flags_mark_every_tool() -> None:
    tools = AgentTools(
        factory=_tools,
        gateway=False,
        people_only=True,
        seeing=frozenset({"look"}),
        timeout=5,
    )
    built = tools.build(_context())

    assert built.timeout == 5
    assert built.tools["look"].metadata == {
        "effect": "read",
        HOST_FLAGS: asdict(ToolFlags(people_only=True, seeing=True)),
    }
    assert built.tools["look"].prepare is _offer_nothing


async def _gate(tool_def: ToolDefinition) -> dict[str, str]:
    args = {"text": "a"}
    call = ToolCallPart(tool_def.name, args)

    context = _context()

    return await in_run(
        context.deps,
        HOST_GATE.before_tool_execute(context, call=call, tool_def=tool_def, args=args),
    )


async def test_the_gate_hides_what_a_mode_does_not_offer_and_asks_before_a_write() -> (
    None
):
    built = AgentTools(factory=_tools).build(_context())
    tool_defs = [tool.tool_def for tool in built.tools.values()]
    read = run_context(UserDeps(user=User(id="u"), mode="read"))
    shown = await in_run(read.deps, HOST_GATE.prepare_tools(read, tool_defs))

    assert {tool_def.name for tool_def in shown} == {"echo", "look"}

    with pytest.raises(ApprovalRequired):
        await _gate(built.tools["save"].tool_def)


async def test_a_gateway_write_is_asked_about_by_its_validator_not_the_gate() -> None:
    save = AgentTools(factory=_tools, gateway=True).build(_context()).tools["save"]

    assert save.metadata == {
        "effect": "write",
        HOST_FLAGS: asdict(ToolFlags(gateway=True)),
    }
    assert await _gate(save.tool_def) == {"text": "a"}


async def test_a_bare_item_is_taken_as_a_one_item_list() -> None:
    """Models often send the item itself, which the schema still calls a list."""
    context = _context()
    toolset = FunctionToolset[UserDeps](
        [AgentTools(factory=_tools).build(context).tools["echo"]]
    )
    manager = await ToolManager(
        toolset, root_capability=LenientArguments()
    ).for_run_step(context)

    result = await manager.handle_call(ToolCallPart("echo", {"queries": '"a"'}))

    assert unpack(result).data == ["a"]
    assert (
        toolset.tools["echo"].function_schema.json_schema["properties"]["queries"][
            "type"
        ]
        == "array"
    )


def test_the_data_rides_as_a_chunk_named_by_its_call() -> None:
    # The frontend correlates the data part with its tool part by id,
    # since the AI SDK appends data parts out of positional order.
    out = with_data_chunk(tool_return({"rows": [1, None]}, "x"), "call-xyz")
    chunk = out.metadata

    assert isinstance(chunk, ToolOutputChunk)
    assert chunk.id == "call-xyz"
    assert chunk.data_json() is chunk.data_json()
    assert chunk.encode(5) == BaseChunk.encode(chunk, 5)
    assert out.return_value == "x"

    plain = with_data_chunk(tool_return("plain"), "call-1")

    assert plain.metadata is None
    assert plain.return_value == "plain"
