"""MCP package for Hivegent."""

from .app import mcp_app
from .external import build_mcp_server

__all__ = ["build_mcp_server", "mcp_app"]


def _register_tools() -> None:
    from . import tools


_register_tools()
