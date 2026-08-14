"""Routes for the generic agent-tool debugging console.

Lets an administrator invoke any registered agent tool with arbitrary
arguments against their own stores. Its main purpose is exercising stateful
behaviour that unit tests don't cover, such as pgvector retrieval, through
the exact code path the agent uses.
"""

import time
import traceback
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import ValidationError

from ...agents import (
    UserDeps,
    collect_tool_schemas,
    invoke_agent_tool,
)
from ...auth import User, require_admin
from ...types import ToolRunResult, ToolSchema
from ..common import group_stores, user_store

__all__ = ["router"]

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

    Validation and execution failures are returned as ``ToolRunResult`` with
    ``ok=False`` and a message so they can be inspected in the console rather
    than surfacing as opaque HTTP errors. An unknown tool name is a 404.
    """
    deps = UserDeps(
        user_id=user.id,
        store=user_store(user),
        group_stores=group_stores(user),
        write_group_stores=group_stores(user, writable=True),
    )
    text: str | None = None
    data: Any = None
    error: str | None = None
    ok = False
    start = time.perf_counter()
    try:
        text, data = await invoke_agent_tool(tool_name, args, deps)
        ok = True
    except KeyError as exc:
        raise HTTPException(
            status_code=404, detail=f"Unknown tool: {tool_name}"
        ) from exc
    except ValidationError as exc:
        error = str(exc)
    except Exception:  # noqa: BLE001 - surface any tool failure for debugging
        error = traceback.format_exc()
    return ToolRunResult(
        ok=ok,
        text=text,
        data=data,
        error=error,
        elapsed_ms=(time.perf_counter() - start) * 1000,
    )
