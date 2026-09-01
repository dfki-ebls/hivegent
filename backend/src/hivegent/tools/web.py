"""Web search and fetch tool callables."""

import logging
import re
from dataclasses import dataclass, field
from email.utils import parseaddr
from importlib.metadata import metadata
from typing import Annotated, ClassVar, override
from urllib.parse import quote

import httpx2
from bs4 import BeautifulSoup
from markdownify import MarkdownConverter
from pydantic import Field

from ..security import UnsafeUrlError
from .base import ToolOutput, ToolRetry
from .formatting import BLOCK_SEP, cap_lines, hint_suffix, iter_annotated
from .sink import OutputPathArg, RedirectedOutput, RedirectingTool

__all__ = [
    "WebFetch",
    "WebMaxResultsArg",
    "WebPage",
    "WebQueryArg",
    "WebSearch",
    "WebUrlArg",
    "build_user_agent",
]

logger = logging.getLogger(__name__)


def build_user_agent(contact: str = "") -> str:
    """Build a descriptive User-Agent identifying the app and an operator.

    Wikipedia (and other well-behaved hosts) reject requests carrying a
    generic library agent — the default ``python-httpx/...`` earns an
    HTTP 403 — so identify the application and a contact address, as
    Wikimedia's User-Agent policy asks.  *contact* is the operator email
    advertised for traffic questions; it falls back to the package
    author when empty.
    """
    meta = metadata("hivegent")
    contact = contact or parseaddr(meta.get("Author-email", ""))[1]
    suffix = f" (+mailto:{contact})" if contact else ""
    return f"{meta['Name']}/{meta['Version']}{suffix}"


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


def _snippet_text(html: str) -> str:
    """Reduce a MediaWiki search snippet (highlighted HTML) to plain text."""
    return " ".join(BeautifulSoup(html, "html.parser").get_text().split())


@dataclass(slots=True, frozen=True)
class WebSearch(RedirectingTool[list[dict[str, str]]]):
    """Search Wikipedia for up-to-date information.

    Queries the official MediaWiki API through the egress proxy with no
    scraping, bot detection, or search-engine rate limits.
    It only returns ``wikipedia.org`` links.
    ``language`` selects the edition, whose host the client checks against its
    URL policy like every request and redirect.

    ``client`` is the pooled, policy-checked web client; it is owned by the
    application lifespan, so this tool uses it without ever closing it.
    """

    injectable: ClassVar[bool] = True
    """The sandbox has no network, so a program can only be handed this."""

    client: httpx2.AsyncClient
    language: str = "en"
    timeout_seconds: float = 10.0
    user_agent: str = field(default_factory=build_user_agent)

    @override
    async def __call__(
        self,
        query: WebQueryArg,
        max_results: WebMaxResultsArg = 5,
        output_path: OutputPathArg = None,
    ) -> ToolOutput[list[dict[str, str]] | RedirectedOutput]:
        """Search Wikipedia (and only Wikipedia) for up-to-date information.

        This searches the Wikipedia encyclopedia exclusively, not the
        open web: every result is a Wikipedia article, so it is the tool
        for encyclopedic facts, definitions, and background, but it
        cannot find news, forums, product pages, or any other site.

        Returns a list of results with ``title``, ``href``, and ``body``
        fields. ``body`` is only a short snippet, so follow up with
        ``web_fetch`` on a result's ``href`` to read the full article.
        """
        max_results = min(max(1, max_results), 20)
        endpoint = f"https://{self.language}.wikipedia.org/w/api.php"
        params = {
            "action": "query",
            "list": "search",
            "srsearch": query,
            "srlimit": max_results,
            "srprop": "snippet",
            "format": "json",
            "formatversion": "2",
        }
        try:
            response = await self.client.get(
                endpoint,
                params=params,
                timeout=self.timeout_seconds,
                headers={"User-Agent": self.user_agent},
            )
            response.raise_for_status()
            hits = response.json().get("query", {}).get("search", [])
        except (httpx2.HTTPError, UnsafeUrlError) as exc:
            logger.warning("Web search failed for query %r: %s", query, exc)
            raise ToolRetry(
                "web search failed — the Wikipedia API may be unavailable or "
                "the query too narrow; try again or rephrase the query."
            ) from exc
        results = [
            {
                "title": hit.get("title", ""),
                "href": f"https://{self.language}.wikipedia.org/wiki/"
                + quote(hit.get("title", "").replace(" ", "_")),
                "body": _snippet_text(hit.get("snippet", "")),
            }
            for hit in hits
        ]
        blocks: list[str] = []
        for i, r in enumerate(results, 1):
            block = f"[{i}] {r['title']} ({r['href']})"
            if r["body"]:
                block += f"\n    {r['body']}"
            blocks.append(block)
        formatted = BLOCK_SEP.join(blocks) if results else "(no results)"

        return await self.redirect(
            ToolOutput(data=results, formatted=formatted), output_path
        )


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
class WebFetch(RedirectingTool[WebPage]):
    """Fetch a web page and return its readable content.

    ``max_response_bytes`` caps how many raw bytes are downloaded per
    page and ``max_chars`` caps the extracted text.  ``max_line_chars``
    truncates each numbered line so a data-URI or minified line cannot
    flood the context, and ``max_formatted_chars`` bounds the rendered
    output as a whole, which neither of the other two does.
    ``client`` is the pooled web client: its request hook validates the URL and
    every redirect hop against the URL host policy, and the egress proxy rejects
    non-public destinations after resolution.  Following redirects and the hop
    limit are both client-level in HTTPX, so they are configured there.  The
    lifespan owns the client, so this tool uses it without ever closing it.
    """

    injectable: ClassVar[bool] = True
    """The sandbox has no network, so a program can only be handed this."""

    client: httpx2.AsyncClient
    timeout_seconds: float = 10.0
    max_response_bytes: int = 5_000_000
    max_chars: int = 100_000
    max_line_chars: int = 2000
    max_formatted_chars: int = 50_000
    user_agent: str = field(default_factory=build_user_agent)

    @override
    async def __call__(
        self, url: WebUrlArg, output_path: OutputPathArg = None
    ) -> ToolOutput[WebPage | RedirectedOutput]:
        """Fetch a web page as readable text.

        HTML is reduced to its markdown text content; plain-text and JSON
        responses pass through unchanged.  Each content line is numbered
        so it can be cited like a document line.  Redirects are followed
        by the client, whose request hook checks every hop against the URL
        host policy before the egress proxy connects.
        """
        try:
            return await self.redirect(await self._fetch(url), output_path)
        except ToolRetry:
            raise
        except httpx2.TimeoutException as exc:
            raise ToolRetry("request timed out.") from exc
        except httpx2.TooManyRedirects as exc:
            raise ToolRetry("too many redirects.") from exc
        except httpx2.HTTPStatusError as exc:
            raise ToolRetry(f"HTTP {exc.response.status_code}.") from exc
        except (
            UnsafeUrlError,
            httpx2.UnsupportedProtocol,
            httpx2.ConnectError,
        ) as exc:
            raise ToolRetry(str(exc)) from exc
        except Exception as exc:
            logger.exception("Web fetch failed for URL %r", url)
            raise ToolRetry("failed to fetch URL.") from exc

    async def _fetch(self, url: str) -> ToolOutput[WebPage]:
        async with self.client.stream(
            "GET",
            url,
            timeout=self.timeout_seconds,
            headers={"User-Agent": self.user_agent},
        ) as response:
            response.raise_for_status()
            mime = _mime_type(response.headers.get("content-type", ""))
            if not _is_textual(mime) and not _is_html(mime):
                raise ToolRetry(f"unsupported content type '{mime}'.")
            body, truncated = await self._read_capped(response)

        return self._finalize(str(response.url), response, mime, body, truncated)

    async def _read_capped(self, response: httpx2.Response) -> tuple[bytes, bool]:
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
        response: httpx2.Response,
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
        rendered, omitted = cap_lines(
            iter_annotated(content.splitlines(), 1, self.max_line_chars),
            self.max_formatted_chars,
        )
        suffix = hint_suffix(["truncated"] if truncated or omitted else [])
        return ToolOutput(data=page, formatted=f"{header}\n{rendered}{suffix}")
