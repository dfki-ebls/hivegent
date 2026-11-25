"""Routes for the admin debugging consoles, registered only under ``debug.enable``.

The tool debugger lets an administrator invoke any registered agent tool with
arbitrary arguments against their own stores.  Its main purpose is exercising
stateful behaviour that unit tests don't cover, such as pgvector retrieval,
through the exact code path the agent uses.

The chat debugger lets an administrator ask an agent about one of their own
conversations, and condense the answers into a report free of its data.  It is
stateless: the client sends the whole debugging session with every request.
"""

import asyncio
import time
import traceback
from collections.abc import Sequence
from functools import partial
from typing import Annotated, Any

from exar.agents import RunConfig
from exar.policy import allow_all
from exar.pydantic_ai import traced_events
from exar.tools.batch import unpack
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import ValidationError
from pydantic_ai.exceptions import ModelRetry
from pydantic_ai.messages import ModelMessage, UserPromptPart
from pydantic_ai.models import Model
from pydantic_ai.settings import ModelSettings
from pydantic_ai.ui.vercel_ai import VercelAIAdapter

from ...agents import (
    TOOL_NAMES,
    UserDeps,
    collect_tool_schemas,
    debug_agent,
    invoke_agent_tool,
    turn_usage_limits,
)
from ...agents.debugger import DebugDeps, Step, conversation_steps, write_report
from ...agents.tools.conversation import format_messages
from ...auth import require_admin
from ...db.conversations import (
    ConversationSummary,
    OwnedConversation,
    load_conversation,
    load_conversation_summary,
    owned_conversation,
    render_prompt,
)
from ...llm import model_from_config, thinking_model_settings
from ...store import User
from ...types import (
    DebugChatRequest,
    DebugReport,
    DebugReportRequest,
    DebugRequestConfig,
    ToolRunResult,
    ToolSchema,
)
from ..common import found, found_by_id, require_chat_llm
from ..vercel import SDK_VERSION

__all__ = ["router"]

router = APIRouter(prefix="/debug", dependencies=[Depends(require_admin)])

_DEBUG_CONFIG = RunConfig(policy=allow_all)
"""The config of a chat debugger run, whose agent only reads the conversation it analyzes."""


@router.get("/tools")
async def list_tool_schemas() -> list[ToolSchema]:
    """Return every agent tool with the JSON Schema of its parameters."""
    return collect_tool_schemas()


@router.post("/tools/{tool_name}")
async def run_tool(
    tool_name: str,
    args: dict[str, Any],
    user: Annotated[User, Depends(require_admin)],
) -> ToolRunResult:
    """Invoke ``tool_name`` with ``args`` against the caller's own stores.

    The call runs as an agent's does, its validator and write gate included,
    in ``write`` mode, which the gate never asks about.  Validation and
    execution failures are returned as ``ToolRunResult`` with ``ok=False`` and
    a message so they can be inspected in the console rather than surfacing as
    opaque HTTP errors. An unknown tool name is a 404.
    """
    if tool_name not in TOOL_NAMES:
        raise HTTPException(status_code=404, detail=f"Unknown tool: {tool_name}")

    deps = UserDeps(user=user, mode="write")
    text: str | None = None
    data: Any = None
    error: str | None = None
    ok = False
    start = time.perf_counter()

    try:
        output = unpack(await invoke_agent_tool(tool_name, args, deps))
        text, data, ok = output.text, output.data, True
    except ValidationError as exc:
        error = str(exc)
    except ModelRetry as exc:
        error = exc.message
    except Exception:  # noqa: BLE001 - surface any tool failure for debugging
        error = traceback.format_exc()

    return ToolRunResult(
        ok=ok,
        text=text,
        data=data,
        error=error,
        elapsed_ms=(time.perf_counter() - start) * 1000,
    )


async def _own_conversation(
    conversation_id: str,
    admin: Annotated[User, Depends(require_admin)],
) -> OwnedConversation:
    """The path's conversation, only while it is the admin's own."""
    return await found_by_id(conversation_id, partial(owned_conversation, admin))


type OwnConversation = Annotated[OwnedConversation, Depends(_own_conversation)]


async def _debug_deps(conversation: OwnedConversation) -> DebugDeps:
    """The steps of *conversation* and the tools its agent had.

    The steps are built off the event loop, as they render every tool result.
    """
    data = found(await load_conversation(conversation))
    steps = await asyncio.to_thread(list[Step], conversation_steps(data.messages))

    return DebugDeps(steps, collect_tool_schemas())


def _main_model(config: DebugRequestConfig) -> tuple[Model, ModelSettings]:
    """The chat model *config* names, and its settings."""
    llm = require_chat_llm(config.model, config.reasoning_effort)

    return model_from_config(llm), thinking_model_settings(llm)


def _task(history: Sequence[ModelMessage]) -> str:
    """The last prompt of *history*, the task of a debugger run."""
    prompts = [
        part
        for message in history
        for part in message.parts
        if isinstance(part, UserPromptPart)
    ]

    return render_prompt(prompts[-1]) if prompts else ""


@router.get("/conversations/{conversation_id}")
async def get_debug_conversation(conversation: OwnConversation) -> ConversationSummary:
    """Return the conversation summary, to confirm the id before debugging."""
    return found(await load_conversation_summary(conversation))


@router.post("/conversations/{conversation_id}/chat")
async def debug_conversation(
    conversation: OwnConversation,
    config: DebugChatRequest,
    request: Request,
) -> Response:
    """Stream the debugger agent's answer about the conversation."""
    deps = await _debug_deps(conversation)
    model, model_settings = _main_model(config)
    adapter = VercelAIAdapter[DebugDeps, str](
        agent=debug_agent,
        run_input=config.run_input(),
        accept=request.headers.get("accept"),
        sdk_version=SDK_VERSION,
    )

    # The run is the root invocation `debug` of an exar run, like a chat turn.
    return adapter.streaming_response(
        adapter.transform_stream(
            traced_events(
                adapter.run_stream_native(
                    deps=deps,
                    model=model,
                    model_settings=model_settings,
                    usage_limits=turn_usage_limits,
                ),
                _task(adapter.messages),
                _DEBUG_CONFIG,
                name="debug",
            )
        )
    )


@router.post("/conversations/{conversation_id}/report")
async def report_conversation(
    conversation: OwnConversation,
    body: DebugReportRequest,
) -> DebugReport:
    """Condense a debugging session into a report free of the conversation's data."""
    deps = await _debug_deps(conversation)
    model, model_settings = _main_model(body)
    session = format_messages(VercelAIAdapter.load_messages(body.messages))

    return await write_report(
        session,
        # Off the event loop, as it reads every word of the conversation.
        await asyncio.to_thread(deps.leak_check),
        model,
        model_settings,
    )
