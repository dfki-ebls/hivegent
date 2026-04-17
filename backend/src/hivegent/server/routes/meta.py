"""Routes for settings, tool metadata, and pipeline metadata."""

import asyncio
from typing import Annotated

from fastapi import APIRouter, Depends

from ...agents import TOOLSET_GROUPS, collect_tool_info
from ...auth import User, get_current_user
from ...chunkers import ChunkingPipelineInfo, get_chunking_pipelines_info
from ...config import settings
from ...converters import ConversionPipelineInfo, get_conversion_pipelines_info
from ...mcp import build_mcp_server
from ...types import (
    McpServerConfig,
    McpTestResponse,
    SettingsResponse,
    ToolInfo,
    UserResponse,
)

__all__ = ["router"]

router = APIRouter()


@router.get("/settings")
async def get_settings(
    user: Annotated[User, Depends(get_current_user)],
) -> SettingsResponse:
    """Get server-side LLM settings and authenticated user metadata."""
    return SettingsResponse(
        model=settings.llm.model,
        aux_model=settings.llm.aux_model,
        has_api_key=bool(settings.llm.api_key),
        base_url=settings.llm.base_url,
        user=UserResponse.from_user(user),
    )


@router.get("/tools")
async def list_tools(
    _user: Annotated[User, Depends(get_current_user)],
) -> list[ToolInfo]:
    """Return metadata for all available agent tools."""
    return collect_tool_info(TOOLSET_GROUPS)


@router.post("/mcp/test")
async def test_mcp_server(
    config: McpServerConfig,
    _user: Annotated[User, Depends(get_current_user)],
) -> McpTestResponse:
    """Test connectivity to an MCP server and return the discovered tool count."""
    mcp_server = build_mcp_server(config)
    try:
        async with asyncio.timeout(10):
            async with mcp_server:
                tools = await mcp_server.list_tools()
                return McpTestResponse(ok=True, tool_count=len(tools))
    except Exception as exc:
        return McpTestResponse(ok=False, error=str(exc))


@router.get("/pipelines/conversion")
async def list_conversion_pipelines() -> list[ConversionPipelineInfo]:
    """Get metadata for all conversion pipelines."""
    return get_conversion_pipelines_info()


@router.get("/pipelines/chunking")
async def list_chunking_pipelines() -> list[ChunkingPipelineInfo]:
    """Get metadata for all chunking pipelines."""
    return get_chunking_pipelines_info()
