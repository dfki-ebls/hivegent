"""Routes for settings, tool metadata, and pipeline metadata."""

import asyncio
import logging
from collections.abc import Sequence
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException

from ...agents import collect_tool_schemas
from ...auth import User, get_current_user
from ...chunkers import ChunkingPipelineInfo, get_chunking_pipelines_info
from ...config import settings
from ...converters import (
    INGESTIBLE_IMAGE_MEDIA_TYPES,
    ConversionPipelineInfo,
    get_conversion_pipelines_info,
)
from ...mcp import build_mcp_toolset, validate_mcp_servers
from ...types import (
    AttachmentLimits,
    McpServerConfig,
    McpTestResponse,
    SettingsResponse,
    ToolInfo,
    UserResponse,
)

__all__ = ["router"]

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/settings")
async def get_settings(
    user: Annotated[User, Depends(get_current_user)],
) -> SettingsResponse:
    """Get server-side LLM settings and authenticated user metadata."""
    return SettingsResponse(
        model=settings.llm.model,
        aux_model=settings.llm.aux_model,
        stt_model=settings.llm.stt_model,
        has_api_key=bool(settings.llm.api_key),
        base_url=settings.llm.base_url,
        user=UserResponse.from_user(user),
        attachments=AttachmentLimits(
            media_types=sorted(INGESTIBLE_IMAGE_MEDIA_TYPES),
            max_bytes=settings.limits.max_attachment_bytes,
        ),
    )


@router.get("/tools")
async def list_tools(
    _user: Annotated[User, Depends(get_current_user)],
) -> Sequence[ToolInfo]:
    """Return metadata for all available agent tools.

    The ``ToolInfo`` response model drops the parameter schemas that
    :func:`collect_tool_schemas` also gathers; the debug console fetches
    those separately.
    """
    return collect_tool_schemas()


@router.post("/mcp/test")
async def test_mcp_server(
    config: McpServerConfig,
    _user: Annotated[User, Depends(get_current_user)],
) -> McpTestResponse:
    """Test connectivity to an MCP server and return the discovered tool count."""
    try:
        await validate_mcp_servers([config])
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    mcp_toolset = build_mcp_toolset(config)
    try:
        async with asyncio.timeout(10):
            async with mcp_toolset:
                tools = await mcp_toolset.list_tools()
                return McpTestResponse(ok=True, tool_count=len(tools))
    except Exception as exc:
        # A timeout raises a bare TimeoutError whose str() is empty, so fall back
        # to the type name; log the full traceback so the operator can diagnose
        # what the user-facing message alone cannot convey.
        logger.warning("MCP server test failed for %s", config.url, exc_info=True)
        return McpTestResponse(ok=False, error=str(exc) or type(exc).__name__)


@router.get("/pipelines/conversion")
async def list_conversion_pipelines() -> list[ConversionPipelineInfo]:
    """Get metadata for all conversion pipelines."""
    return get_conversion_pipelines_info()


@router.get("/pipelines/chunking")
async def list_chunking_pipelines() -> list[ChunkingPipelineInfo]:
    """Get metadata for all chunking pipelines."""
    return get_chunking_pipelines_info()
