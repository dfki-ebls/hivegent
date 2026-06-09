"""Web search and fetch tool callables."""

import logging
from dataclasses import dataclass
from typing import Annotated, override

import httpx
from ddgs import DDGS
from pydantic import Field

from ..config import settings
from ..security import (
    UnsafeUrlError,
    create_safe_async_client,
    validate_external_url_async,
)
from .base import AsyncTool, SyncTool, ToolOutput, ToolRetry
from .formatting import BLOCK_SEP

__all__ = [
    "WebFetch",
    "WebMaxResultsArg",
    "WebQueryArg",
    "WebSearch",
    "WebUrlArg",
]

logger = logging.getLogger(__name__)

WebQueryArg = Annotated[
    str,
    Field(description="Search query string."),
]
WebMaxResultsArg = Annotated[
    int,
    Field(description="Maximum number of search results to return.", ge=1, le=20),
]
WebUrlArg = Annotated[
    str,
    Field(description="HTTP or HTTPS URL to fetch."),
]


@dataclass(slots=True, frozen=True)
class WebSearch(SyncTool[list[dict[str, str]]]):
    """Search the web using DuckDuckGo."""

    @override
    def __call__(
        self,
        query: WebQueryArg,
        max_results: WebMaxResultsArg = 5,
    ) -> ToolOutput[list[dict[str, str]]]:
        """Search the web using DuckDuckGo for up-to-date information.

        Returns a list of results with ``title``, ``href``, and ``body`` fields.
        """
        max_results = min(max(1, max_results), 20)
        try:
            with DDGS() as ddgs:
                raw = list(
                    ddgs.text(query, max_results=max_results, backend="duckduckgo")
                )
            results = [
                {
                    "title": r.get("title", ""),
                    "href": r.get("href", ""),
                    "body": r.get("body", ""),
                }
                for r in raw
            ]
        except Exception:
            logger.exception("Web search failed for query %r", query)
            results = []
        if not results:
            return ToolOutput(data=results, formatted="(no results)")
        blocks: list[str] = []
        for i, r in enumerate(results, 1):
            block = f"[{i}] {r.get('title', '')} ({r.get('href', '')})"
            body = r.get("body", "")
            if body:
                block += f"\n    {body}"
            blocks.append(block)
        return ToolOutput(data=results, formatted=BLOCK_SEP.join(blocks))


@dataclass(slots=True, frozen=True)
class WebFetch(AsyncTool[str]):
    """Fetch web page content as plain text.

    Timeout, response cap, and redirect limit are read from
    ``settings.network`` so deployments can tune them via
    ``HIVEGENT_NETWORK__*`` environment variables.
    """

    @override
    async def __call__(self, url: WebUrlArg) -> ToolOutput[str]:
        """Fetch the content of a web page as plain text.

        Follows redirects manually, re-validating each hop against the
        SSRF filter so a public URL cannot redirect to a private IP.
        """
        try:
            return await self._fetch(url)
        except ToolRetry:
            raise
        except httpx.TimeoutException as exc:
            raise ToolRetry("request timed out.") from exc
        except httpx.HTTPStatusError as exc:
            raise ToolRetry(f"HTTP {exc.response.status_code}.") from exc
        except UnsafeUrlError as exc:
            raise ToolRetry(str(exc)) from exc
        except Exception as exc:
            logger.exception("Web fetch failed for URL %r", url)
            raise ToolRetry("failed to fetch URL.") from exc

    async def _fetch(self, url: str) -> ToolOutput[str]:
        current = url
        async with create_safe_async_client(
            timeout=settings.network.webfetch_timeout_seconds,
            follow_redirects=False,
        ) as client:
            for _ in range(settings.network.webfetch_max_redirects):
                await validate_external_url_async(current)
                response = await client.get(current)
                if not response.is_redirect or response.next_request is None:
                    return self._finalize(response)
                current = str(response.next_request.url)
            raise ToolRetry("too many redirects.")

    def _finalize(self, response: httpx.Response) -> ToolOutput[str]:
        response.raise_for_status()
        content_type = response.headers.get("content-type", "")
        if "text/" not in content_type and "application/json" not in content_type:
            raise ToolRetry(f"unsupported content type '{content_type}'.")
        body = response.text
        cap = settings.network.webfetch_max_response_bytes
        if len(body) > cap:
            body = body[:cap] + "\n\n[truncated]"
        return ToolOutput(data=body)
