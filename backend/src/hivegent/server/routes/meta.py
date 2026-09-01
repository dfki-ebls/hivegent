"""Routes for settings, tool metadata, and pipeline metadata."""

import asyncio
import logging
from collections.abc import Sequence
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException

from ...agents import collect_tool_schemas, unlisted_tool_names
from ...auth import User, get_current_user
from ...chunkers import (
    ChunkingPipeline,
    ChunkingPipelineInfo,
    get_chunking_pipeline_config,
    get_chunking_pipelines_info,
)
from ...config import settings
from ...converters import (
    INGESTIBLE_IMAGE_MEDIA_TYPES,
    ConversionPipeline,
    ConversionPipelineInfo,
    get_conversion_pipeline_config,
    get_conversion_pipelines_info,
)
from ...mcp import build_mcp_toolset, validate_mcp_servers
from ...pipeline_registry import PipelineConfigInfo
from ...types import (
    AttachmentLimits,
    McpServerConfig,
    McpTestResponse,
    SettingsResponse,
    ToolInfo,
    ToolsSpec,
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
    """Return metadata for the agent tools a user may switch on and off.

    The ``ToolInfo`` response model drops the parameter schemas that
    :func:`collect_tool_schemas` also gathers; the debug console fetches
    those separately, unfiltered, since that is where an operator reads the
    name to exclude.

    Whatever the model's tool list withholds is dropped here, because this
    listing is what the settings dialog renders a checkbox per: a tool the
    deployment withholds must not offer the user a switch that does nothing.
    That is :func:`unlisted_tool_names` and not the operator's exclusions alone,
    since a ``sandbox_only`` tool has no schema to toggle either — it is
    reachable from a program and from nowhere the user can switch.
    """
    hidden = unlisted_tool_names(ToolsSpec())
    return [tool for tool in collect_tool_schemas() if tool.name not in hidden]


@router.post("/mcp/test")
async def test_mcp_server(
    config: McpServerConfig,
    _user: Annotated[User, Depends(get_current_user)],
) -> McpTestResponse:
    """Test connectivity to an MCP server and return the discovered tool count."""
    try:
        validate_mcp_servers([config])
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


@router.get("/pipelines/conversion/{pipeline}/config")
def get_conversion_config(
    pipeline: ConversionPipeline,
) -> PipelineConfigInfo:
    """Get configuration metadata for one conversion pipeline.

    Synchronous so FastAPI runs it in the threadpool: the first request for a
    pipeline imports its backend, which must not block the event loop.
    """
    try:
        return get_conversion_pipeline_config(pipeline)
    except (ImportError, ValueError) as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/pipelines/chunking")
async def list_chunking_pipelines() -> list[ChunkingPipelineInfo]:
    """Get metadata for all chunking pipelines."""
    return get_chunking_pipelines_info()


@router.get("/pipelines/chunking/{pipeline}/config")
def get_chunking_config(
    pipeline: ChunkingPipeline,
) -> PipelineConfigInfo:
    """Get configuration metadata for one chunking pipeline.

    Synchronous for the same reason as the conversion route above.
    """
    try:
        return get_chunking_pipeline_config(pipeline)
    except (ImportError, ValueError) as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
