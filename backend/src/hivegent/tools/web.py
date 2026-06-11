"""Web search and fetch tool callables."""

import logging
import re
from dataclasses import dataclass, field
from typing import Annotated, override

import httpx
from bs4 import BeautifulSoup
from ddgs import DDGS
from ddgs.exceptions import DDGSException
from markdownify import MarkdownConverter
from pydantic import Field

from ..security import (
    UnsafeUrlError,
    UrlPolicy,
    create_safe_async_client,
    is_safe_external_url,
)
from .base import AsyncTool, SyncTool, ToolOutput, ToolRetry
from .formatting import BLOCK_SEP, annotate_lines

__all__ = [
    "WebFetch",
    "WebMaxResultsArg",
    "WebPage",
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
    """Search the web for up-to-date information.

    ``backend`` is the ddgs engine selection (``auto`` rotates across
    several engines so one blocked provider does not take the tool down)
    and ``region`` the ddgs region code (e.g. ``de-de``).  Result URLs
    that violate ``policy`` are dropped.
    """

    backend: str = "auto"
    region: str = "us-en"
    policy: UrlPolicy = field(default_factory=UrlPolicy)

    @override
    def __call__(
        self,
        query: WebQueryArg,
        max_results: WebMaxResultsArg = 5,
    ) -> ToolOutput[list[dict[str, str]]]:
        """Search the web for up-to-date information.

        Returns a list of results with ``title``, ``href``, and ``body``
        fields; ``body`` is only a short snippet, so follow up with
        ``web_fetch`` on a result's ``href`` to read the full page.
        """
        max_results = min(max(1, max_results), 20)
        try:
            with DDGS() as ddgs:
                raw = ddgs.text(
                    query,
                    max_results=max_results,
                    backend=self.backend,
                    region=self.region,
                )
        except DDGSException as exc:
            # ddgs raises for backend failures and empty result sets alike,
            # so surface both as a retryable miss instead of a fake success.
            logger.warning("Web search failed for query %r: %s", query, exc)
            raise ToolRetry(
                "web search returned nothing — the search backends may be "
                "unavailable or the query too narrow; try different terms."
            ) from exc
        except Exception as exc:
            logger.exception("Web search failed for query %r", query)
            raise ToolRetry("web search backend failed.") from exc
        results = [
            {
                "title": r.get("title", ""),
                "href": r.get("href", ""),
                "body": r.get("body", ""),
            }
            for r in raw
            # Only surface results the fetch tool would also accept.
            if is_safe_external_url(r.get("href", ""), policy=self.policy)
        ]
        if not results:
            return ToolOutput(data=results, formatted="(no results)")
        blocks: list[str] = []
        for i, r in enumerate(results, 1):
            block = f"[{i}] {r['title']} ({r['href']})"
            if r["body"]:
                block += f"\n    {r['body']}"
            blocks.append(block)
        return ToolOutput(data=results, formatted=BLOCK_SEP.join(blocks))


@dataclass(slots=True, frozen=True)
class WebPage:
    """Readable content extracted from a fetched web page.

    ``url`` is the final URL after redirects; ``content`` is the page
    reduced to markdown (for HTML) or its raw text (for plain-text and
    JSON responses).
    """

    url: str
    title: str
    content: str
    truncated: bool


def _mime_type(content_type: str) -> str:
    """Return the bare lowercase mime type from a Content-Type header."""
    return content_type.split(";")[0].strip().lower()


def _is_html(mime: str) -> bool:
    return mime in ("text/html", "application/xhtml+xml")


def _is_textual(mime: str) -> bool:
    return (
        mime.startswith("text/")
        or mime in ("application/json", "application/xml")
        or mime.endswith(("+json", "+xml"))
    )


def _html_to_markdown(body: bytes) -> tuple[str, str]:
    """Extract ``(title, markdown)`` from raw HTML bytes.

    BeautifulSoup sniffs the document encoding; non-content elements are
    dropped so the model reads prose, not markup.
    """
    soup = BeautifulSoup(body, "lxml")
    title = soup.title.get_text(strip=True) if soup.title is not None else ""
    for tag in soup(
        ["head", "script", "style", "noscript", "template", "iframe", "svg"]
    ):
        tag.decompose()
    converter = MarkdownConverter(
        heading_style="ATX",
        escape_asterisks=False,
        escape_underscores=False,
        escape_misc=False,
    )
    markdown = converter.convert_soup(soup)
    return title, re.sub(r"\n{3,}", "\n\n", markdown).strip()


@dataclass(slots=True, frozen=True)
class WebFetch(AsyncTool[WebPage]):
    """Fetch a web page and return its readable content.

    ``max_response_bytes`` caps how many raw bytes are downloaded per
    page and ``max_chars`` caps the extracted text.  The safe transport
    validates the requested URL and every redirect hop against
    ``policy``.
    """

    timeout_seconds: float = 10.0
    max_response_bytes: int = 5_000_000
    max_chars: int = 100_000
    max_redirects: int = 5
    policy: UrlPolicy = field(default_factory=UrlPolicy)

    @override
    async def __call__(self, url: WebUrlArg) -> ToolOutput[WebPage]:
        """Fetch a web page as readable text.

        HTML is reduced to its markdown text content; plain-text and JSON
        responses pass through unchanged.  Each content line is numbered
        so it can be cited like a document line.  Redirects are followed
        by the client; the safe transport re-validates every hop against
        the SSRF filter and the URL host policy so a public URL cannot
        redirect somewhere disallowed.
        """
        try:
            return await self._fetch(url)
        except ToolRetry:
            raise
        except httpx.TimeoutException as exc:
            raise ToolRetry("request timed out.") from exc
        except httpx.TooManyRedirects as exc:
            raise ToolRetry("too many redirects.") from exc
        except httpx.HTTPStatusError as exc:
            raise ToolRetry(f"HTTP {exc.response.status_code}.") from exc
        except (UnsafeUrlError, httpx.UnsupportedProtocol, httpx.ConnectError) as exc:
            raise ToolRetry(str(exc)) from exc
        except Exception as exc:
            logger.exception("Web fetch failed for URL %r", url)
            raise ToolRetry("failed to fetch URL.") from exc

    async def _fetch(self, url: str) -> ToolOutput[WebPage]:
        async with create_safe_async_client(
            policy=self.policy,
            timeout=self.timeout_seconds,
            follow_redirects=True,
            max_redirects=self.max_redirects,
        ) as client:
            async with client.stream("GET", url) as response:
                response.raise_for_status()
                mime = _mime_type(response.headers.get("content-type", ""))
                if not _is_textual(mime) and not _is_html(mime):
                    raise ToolRetry(f"unsupported content type '{mime}'.")
                body, truncated = await self._read_capped(response)
            return self._finalize(str(response.url), response, mime, body, truncated)

    async def _read_capped(self, response: httpx.Response) -> tuple[bytes, bool]:
        """Stream the response body up to the configured byte cap."""
        buffer = bytearray()
        async for chunk in response.aiter_bytes():
            buffer.extend(chunk)
            if len(buffer) > self.max_response_bytes:
                return bytes(buffer[: self.max_response_bytes]), True
        return bytes(buffer), False

    def _finalize(
        self,
        url: str,
        response: httpx.Response,
        mime: str,
        body: bytes,
        truncated: bool,
    ) -> ToolOutput[WebPage]:
        if _is_html(mime):
            title, content = _html_to_markdown(body)
        else:
            title = ""
            encoding = response.charset_encoding or "utf-8"
            try:
                content = body.decode(encoding, errors="replace")
            except LookupError:
                content = body.decode("utf-8", errors="replace")
            content = content.strip()

        if len(content) > self.max_chars:
            content = content[: self.max_chars]
            truncated = True

        page = WebPage(url=url, title=title, content=content, truncated=truncated)
        if not content:
            return ToolOutput(data=page, formatted="(no readable text on this page)")
        header = f"{title} — {url}" if title else url
        suffix = "\n\n[truncated]" if truncated else ""
        return ToolOutput(
            data=page,
            formatted=f"{header}\n{annotate_lines(content.splitlines())}{suffix}",
        )
