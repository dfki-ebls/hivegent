"""MCP package for Hivegent."""

from .app import mcp_app
from .external import build_mcp_server
from . import tools as _tools  # noqa: F401

__all__ = ["build_mcp_server", "mcp_app"]
