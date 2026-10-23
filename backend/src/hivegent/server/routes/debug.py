"""Routes for the admin debugging consoles.

The tool debugger lets an administrator invoke any registered agent tool with
arbitrary arguments against their own stores.  Its main purpose is exercising
stateful behaviour that unit tests don't cover, such as pgvector retrieval,
through the exact code path the agent uses.

The chat debugger lets an administrator ask an agent about any user's
conversation, and condense the answers into a report free of its data.  It is
stateless: the client sends the whole debugging session with every request.
"""

import asyncio
import logging
import time
import traceback
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import ValidationError
from pydantic_ai.exceptions import ModelRetry
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
    any_conversation,
    load_conversation,
    load_conversation_summary,
)
from ...llm import model_from_config, thinking_model_settings
from ...store import User
from ...tools.pydantic_ai import unwrap_tool_output
from ...types import (
    DebugReport,
    DebugReportRequest,
    DebugRequestConfig,
    ToolRunResult,
    ToolSchema,
)
from ..common import found, prepare_llm_config
from ..vercel import SDK_VERSION

__all__ = ["router"]

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/debug")


@router.get("/tools")
async def list_tool_schemas(
    _user: Annotated[User, Depends(require_admin)],
) -> list[ToolSchema]:
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
        output = unwrap_tool_output(await invoke_agent_tool(tool_name, args, deps))
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


async def _any_conversation(
    conversation_id: str,
    _admin: Annotated[User, Depends(require_admin)],
) -> OwnedConversation:
    """The path's conversation as its owner's, whoever that is, for an admin."""
    return found(await any_conversation(conversation_id))


type AnyConversation = Annotated[OwnedConversation, Depends(_any_conversation)]


async def _debug_deps(admin: User, conversation: OwnedConversation) -> DebugDeps:
    """The steps of *conversation* and the tools its agent had.

    Logged, since it hands another user's conversation to *admin*.  The steps
    are built off the event loop, as they render every tool result.
    """
    data = found(await load_conversation(conversation))
    logger.info("admin %r debugging conversation %r", admin.id, conversation.id)
    steps = await asyncio.to_thread(list[Step], conversation_steps(data.messages))

    return DebugDeps(steps, collect_tool_schemas())


def _main_model(config: DebugRequestConfig) -> tuple[Model, ModelSettings]:
    """The main tier's model *config* overrides, and its settings."""
    llm = prepare_llm_config(config.llm, tier="main")

    return model_from_config(llm), thinking_model_settings(config.reasoning_effort, llm)


@router.get("/conversations/{conversation_id}")
async def get_debug_conversation(conversation: AnyConversation) -> ConversationSummary:
    """Return any user's conversation summary, to confirm the id before debugging."""
    return found(await load_conversation_summary(conversation))


@router.post("/conversations/{conversation_id}/chat")
async def debug_conversation(
    conversation: AnyConversation,
    request: Request,
    user: Annotated[User, Depends(require_admin)],
) -> Response:
    """Stream the debugger agent's answer about any user's conversation."""
    # The body is the AI SDK's, with the run configuration spliced in beside
    # its messages.
    config = DebugRequestConfig.model_validate(await request.json())
    deps = await _debug_deps(user, conversation)
    model, model_settings = _main_model(config)

    return await VercelAIAdapter[DebugDeps, str].dispatch_request(
        request,
        agent=debug_agent,
        deps=deps,
        model=model,
        model_settings=model_settings,
        usage_limits=turn_usage_limits,
        sdk_version=SDK_VERSION,
    )


@router.post("/conversations/{conversation_id}/report")
async def report_conversation(
    conversation: AnyConversation,
    body: DebugReportRequest,
    user: Annotated[User, Depends(require_admin)],
) -> DebugReport:
    """Condense a debugging session into a report free of the conversation's data."""
    deps = await _debug_deps(user, conversation)
    model, model_settings = _main_model(body)
    session = format_messages(VercelAIAdapter.load_messages(body.messages))

    return await write_report(
        session,
        # Off the event loop, as it reads every word of the conversation.
        await asyncio.to_thread(deps.leak_check),
        model,
        model_settings,
    )
