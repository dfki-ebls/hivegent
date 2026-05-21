"""Memory-oriented agent tool registrations."""

from pydantic_ai import FunctionToolset, RunContext

from ...db.memory import save_memory as _save_memory
from ..common import MemoryContentArg, UserDeps

__all__ = ["memory_toolset", "save_memory"]

memory_toolset: FunctionToolset[UserDeps] = FunctionToolset(defer_loading=False)


@memory_toolset.tool
async def save_memory(ctx: RunContext[UserDeps], content: MemoryContentArg) -> str:
    """Save information to persistent memory that is preserved across conversations.

    Overwrites the entire memory, so always include previously saved information
    you want to retain.
    """
    await _save_memory(ctx.deps.user_id, content)
    return "Memory saved successfully."
