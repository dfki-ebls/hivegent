"""Project-specific tool factory for constructing configured tool instances."""

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from functools import partial
from typing import Literal

import httpx
from pydantic_ai import FilteredToolset, FunctionToolset
from pydantic_ai.mcp import MCPServerStreamableHTTP
from pydantic_ai.toolsets import AbstractToolset

from .chunks import load_chunked_document, rechunk_document
from .config import DOCUMENT_EXTENSION, settings
from .messages import list_conversations as _list_conversations
from .retrieval import search_multi
from .store import Casebase
from .tools import (
    EditDocumentTool,
    GetChunkTool,
    GetDocumentLinesTool,
    GetDocumentTool,
    GlobDocumentsTool,
    GrepTool,
    JqTool,
    ListChunksTool,
    ListDocumentsTool,
    SearchTool,
    WriteDocumentTool,
)
from .types import (
    ChunkSummary,
    ConversationSummary,
    DocumentFilter,
    McpServerConfig,
    ToolInfo,
    ToolsSpec,
)

__all__ = ["ToolFactory", "build_mcp_server", "build_toolsets", "collect_tool_info"]


@dataclass(slots=True, frozen=True)
class ToolFactory:
    """Create configured tool instances for a specific store context."""

    store: Casebase
    document_filter: DocumentFilter | None = None
    group_stores: tuple[Casebase, ...] = ()
    group_filters: dict[str, DocumentFilter] = field(default_factory=dict)

    @property
    def list_documents(self) -> ListDocumentsTool:
        """Create a ListDocumentsTool for the user's document directory."""
        return ListDocumentsTool(
            path=self.store.documents_dir(settings.data_dir),
            extension=DOCUMENT_EXTENSION,
            document_filter=self.document_filter,
        )

    @property
    def get_document(self) -> GetDocumentTool:
        """Create a GetDocumentTool for the user's document directory."""
        return GetDocumentTool(
            path=self.store.documents_dir(settings.data_dir),
            document_filter=self.document_filter,
        )

    @property
    def get_document_lines(self) -> GetDocumentLinesTool:
        """Create a GetDocumentLinesTool for the user's document directory."""
        return GetDocumentLinesTool(
            path=self.store.documents_dir(settings.data_dir),
            document_filter=self.document_filter,
        )

    @property
    def glob_documents(self) -> GlobDocumentsTool:
        """Create a GlobDocumentsTool for the user's document directory."""
        return GlobDocumentsTool(
            path=self.store.documents_dir(settings.data_dir),
            extension=DOCUMENT_EXTENSION,
            document_filter=self.document_filter,
        )

    @property
    def grep(self) -> GrepTool:
        """Create a GrepTool for the user's document directory."""
        return GrepTool(
            path=self.store.documents_dir(settings.data_dir),
            document_filter=self.document_filter,
        )

    def _make_search(self, search_type: Literal["dense", "sparse"]) -> SearchTool:
        """Create a SearchTool for the given search type.

        Args:
            search_type: ``"dense"`` for vector embeddings, ``"sparse"`` for BM25/FTS.
        """
        return SearchTool(
            search_fn=partial(
                search_multi,
                (self.store, *self.group_stores),
                search_type,
                document_filter=self.document_filter,
                group_filters=self.group_filters,
            ),
        )

    @property
    def dense_search(self) -> SearchTool:
        """Create a SearchTool using dense vector embeddings."""
        return self._make_search("dense")

    @property
    def sparse_search(self) -> SearchTool:
        """Create a SearchTool using sparse BM25/FTS matching."""
        return self._make_search("sparse")

    @property
    def list_chunks(self) -> ListChunksTool:
        """Create a ListChunksTool for the user's chunks directory."""
        chunks_dir = self.store.chunks_dir(settings.data_dir)

        def _loader(filename: str) -> Sequence[ChunkSummary] | None:
            chunked = load_chunked_document(chunks_dir, filename)
            if not chunked:
                return None
            return [
                ChunkSummary(
                    token_count=c.token_count,
                    start_index=c.start_index,
                    end_index=c.end_index,
                )
                for c in chunked.chunks
            ]

        return ListChunksTool(
            loader=_loader,
            document_filter=self.document_filter,
        )

    @property
    def get_chunk(self) -> GetChunkTool:
        """Create a GetChunkTool for the user's chunks directory."""
        chunks_dir = self.store.chunks_dir(settings.data_dir)

        def _loader(filename: str, chunk_index: int) -> str | None:
            chunked = load_chunked_document(chunks_dir, filename)
            if not chunked:
                return None
            if 0 <= chunk_index < len(chunked.chunks):
                return chunked.chunks[chunk_index].text
            return None

        return GetChunkTool(
            loader=_loader,
            document_filter=self.document_filter,
        )

    @property
    def edit_document(self) -> EditDocumentTool:
        """Create an EditDocumentTool with automatic re-chunking."""
        store = self.store

        async def _on_write(filename: str) -> None:
            await rechunk_document(store, filename)

        return EditDocumentTool(
            path=store.documents_dir(settings.data_dir),
            document_filter=self.document_filter,
            on_write=_on_write,
        )

    @property
    def write_document(self) -> WriteDocumentTool:
        """Create a WriteDocumentTool with automatic re-chunking."""
        store = self.store

        async def _on_write(filename: str) -> None:
            await rechunk_document(store, filename)

        return WriteDocumentTool(
            path=store.documents_dir(settings.data_dir),
            extension=DOCUMENT_EXTENSION,
            document_filter=self.document_filter,
            on_write=_on_write,
        )

    @property
    def list_conversations(self) -> Callable[[], list[ConversationSummary]]:
        """Create a callable that lists conversations for the user."""
        return partial(_list_conversations, self.store.id)

    @property
    def query_conversations(self) -> JqTool:
        """Create a JqTool for querying conversation JSON files."""
        return JqTool(
            path=self.store.conversations_dir(settings.data_dir),
        )


def build_mcp_server(server_cfg: McpServerConfig) -> MCPServerStreamableHTTP:
    """Build an MCP server toolset from a user-provided config.

    Uses Streamable HTTP transport.  When OAuth2 client credentials are
    configured, the connection is authenticated via
    ``ClientCredentialsOAuthProvider``.

    Args:
        server_cfg: User-provided MCP server configuration.

    Returns:
        A configured ``MCPServerStreamableHTTP`` instance.
    """
    if server_cfg.oauth2:
        import warnings

        from fastmcp.client.auth.oauth import TokenStorageAdapter
        from key_value.aio.stores.memory import MemoryStore
        from mcp.client.auth.extensions.client_credentials import (
            ClientCredentialsOAuthProvider,
        )

        store = MemoryStore()
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            storage = TokenStorageAdapter(store, server_url=server_cfg.url)
        oauth_provider = ClientCredentialsOAuthProvider(
            server_url=server_cfg.url,
            storage=storage,
            client_id=server_cfg.oauth2.client_id,
            client_secret=server_cfg.oauth2.client_secret,
            scopes=server_cfg.oauth2.scopes,
        )
        http_client = httpx.AsyncClient(auth=oauth_provider)
        return MCPServerStreamableHTTP(
            url=server_cfg.url,
            http_client=http_client,
            tool_prefix=server_cfg.tool_prefix,
        )

    return MCPServerStreamableHTTP(
        url=server_cfg.url,
        headers=server_cfg.headers or {},
        tool_prefix=server_cfg.tool_prefix,
    )


def build_toolsets[T](
    toolsets: Sequence[FunctionToolset[T]],
    tools_spec: ToolsSpec,
) -> Sequence[AbstractToolset[T]]:
    """Apply disabled-tool filtering and append MCP server toolsets.

    Args:
        toolsets: Built-in agent toolsets.
        tools_spec: Combined tool configuration from the chat request.

    Returns:
        Sequence of toolsets ready to pass to the agent.
    """
    result: list[AbstractToolset[T]] = []

    if tools_spec.disabled_tools:
        disabled = frozenset(tools_spec.disabled_tools)
        result.extend(
            FilteredToolset(
                wrapped=ts,
                filter_func=lambda _ctx, td, _disabled=disabled: (
                    td.name not in _disabled
                ),
            )
            for ts in toolsets
        )
    else:
        result.extend(toolsets)

    for server_cfg in tools_spec.mcp_servers:
        result.append(build_mcp_server(server_cfg))

    return result


def collect_tool_info[T](
    toolset_groups: dict[str, FunctionToolset[T]],
) -> list[ToolInfo]:
    """Collect metadata from all registered toolset groups.

    Args:
        toolset_groups: Mapping of group name to toolset.

    Returns:
        Flat list of tool info entries.
    """
    result: list[ToolInfo] = []
    for group, toolset in toolset_groups.items():
        for name, tool in toolset.tools.items():
            result.append(
                ToolInfo(name=name, description=tool.description or "", group=group)
            )
    return result
