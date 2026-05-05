"""Memory-oriented agent tool registrations."""

from pydantic_ai import FunctionToolset, RunContext

from ...memory import save_memory as _save_memory
from ..common import MemoryContentArg, UserDeps

__all__ = ["memory_toolset", "save_memory"]

memory_toolset: FunctionToolset[UserDeps] = FunctionToolset(defer_loading=False)


@memory_toolset.tool
def save_memory(_ctx: RunContext[UserDeps], content: MemoryContentArg) -> str:
    """Save information to persistent memory that is preserved across conversations.

    Overwrites the entire memory, so always include previously saved information
    you want to retain.
    """
    _save_memory(_ctx.deps.user_id, content)
    return "Memory saved successfully."
