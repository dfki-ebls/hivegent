"""Routes for settings, tool metadata, and pipeline metadata."""

import asyncio
import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException

from ...agents import collect_tool_schemas, unlisted_tool_names
from ...auth import get_current_user
from ...chunkers import (
    ChunkingPipeline,
    get_chunking_pipeline_config,
    get_chunking_pipelines,
)
from ...config import settings
from ...converters import (
    INGESTIBLE_IMAGE_MEDIA_TYPES,
    ConversionPipeline,
    get_conversion_pipeline_config,
    get_conversion_pipelines,
)
from ...mcp import build_mcp_toolset, validate_mcp_servers
from ...pipeline_registry import PipelineConfigInfo
from ...store import User
from ...transparency import get_transparency_config
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

type PipelineStage = Literal["conversion", "chunking"]


@dataclass(frozen=True, slots=True)
class _Stage[P: StrEnum]:
    """How one processing stage lists its pipelines and describes their config."""

    pipelines: Callable[[], list[P]]
    parse: Callable[[str], P]
    describe: Callable[[P], PipelineConfigInfo]

    def config(self, pipeline: str) -> PipelineConfigInfo:
        return self.describe(self.parse(pipeline))


_STAGES: dict[PipelineStage, _Stage[ConversionPipeline] | _Stage[ChunkingPipeline]] = {
    "conversion": _Stage(
        get_conversion_pipelines, ConversionPipeline, get_conversion_pipeline_config
    ),
    "chunking": _Stage(
        get_chunking_pipelines, ChunkingPipeline, get_chunking_pipeline_config
    ),
}


@router.get("/settings")
async def get_settings(
    user: Annotated[User, Depends(get_current_user)],
) -> SettingsResponse:
    """Get server-side LLM settings and authenticated user metadata."""
    return SettingsResponse(
        model=settings.llm.model,
        aux_model=settings.llm.aux_model,
        stt_model=settings.llm.stt_model,
        has_api_key=settings.llm.api_key is not None,
        base_url=settings.llm.base_url,
        user=UserResponse.from_user(user),
        attachments=AttachmentLimits(
            media_types=sorted(INGESTIBLE_IMAGE_MEDIA_TYPES),
            max_bytes=settings.limits.max_attachment_bytes,
            max_count=settings.multimodal.max_images,
        ),
        transparency=get_transparency_config(),
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


@router.get("/pipelines/{stage}")
async def list_pipelines(
    stage: PipelineStage,
) -> list[ConversionPipeline] | list[ChunkingPipeline]:
    """Get the installed pipelines of one processing stage."""
    return _STAGES[stage].pipelines()


@router.get("/pipelines/{stage}/{pipeline}/config")
def get_pipeline_config(stage: PipelineStage, pipeline: str) -> PipelineConfigInfo:
    """Get configuration metadata for one pipeline of a processing stage.

    Synchronous so FastAPI runs it in the threadpool: the first request for a
    pipeline imports its backend, which must not block the event loop. An
    unknown pipeline name is a 404 like an uninstalled one.
    """
    try:
        return _STAGES[stage].config(pipeline)
    except (ImportError, ValueError) as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
