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
from pydantic import ConfigDict, Field

from ..concurrency import bounded_gather
from ..humanize import pluralize
from ..security import UnsafeUrlError
from .base import (
    Batch,
    BatchShare,
    ToolOutput,
    ToolRetry,
    batch_field,
    run_batch,
)
from .formatting import BLOCK_SEP, cap_lines, hint_suffix, iter_annotated
from .sink import OutputPathArg, RedirectedOutput, RedirectingTool

__all__ = [
    "WebEditionArg",
    "WebFetch",
    "WebMaxResultsArg",
    "WebPage",
    "WebQueryArg",
    "WebSearch",
    "WebSearchHit",
    "WebSearchResults",
    "WebSearchesArg",
    "WebUrlsArg",
    "WikipediaSearch",
    "build_user_agent",
]

_WEB_CONCURRENCY = 4
"""Requests one batched web call keeps in flight, polite to a single host."""

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
    meta = metadata("hivegent-backend")
    contact = contact or parseaddr(meta.get("Author-email", ""))[1]
    suffix = f" (+mailto:{contact})" if contact else ""
    return f"{meta['Name']}/{meta['Version']}{suffix}"


WebQueryArg = Annotated[
    str,
    Field(description="Search query string, phrased in the edition's language."),
]
WebEditionArg = Annotated[
    str | None,
    Field(
        description=(
            "Wikipedia edition to search, as its language code (e.g. `en`, "
            "`de`, `fr`). Pick the edition whose language the best sources are "
            "likely written in, and search another one (e.g. English or the "
            "user's language) when results are poor. Omit it for the default "
            "edition."
        ),
        pattern=r"^[a-z]+(-[a-z]+)*$",
        max_length=20,
    ),
]
WebMaxResultsArg = Annotated[
    int,
    Field(description="Maximum number of results to return per search.", ge=1, le=20),
]
WebUrlsArg = Annotated[
    list[str],
    batch_field(
        "HTTP or HTTPS URLs to fetch. Fetch every page you need in one call, as "
        "the output budget is shared between them, and a page that fails is "
        "reported without failing the others."
    ),
]


@dataclass(slots=True, frozen=True)
class WikipediaSearch:
    """One search: the query, and the Wikipedia edition to run it in."""

    __pydantic_config__ = ConfigDict(extra="forbid")

    query: WebQueryArg
    edition: WebEditionArg = None


WebSearchesArg = Annotated[
    list[WikipediaSearch],
    batch_field(
        "Searches to run. Search several editions or phrasings in one call "
        "rather than one call each, and an article several of them find is "
        "listed once."
    ),
]


@dataclass(slots=True, frozen=True)
class WebSearchHit:
    """One Wikipedia article a search found, with its snippet."""

    title: str
    href: str
    body: str


@dataclass(slots=True, frozen=True)
class WebSearchResults:
    """The articles one search found that no earlier search of the call had."""

    query: str
    edition: str
    hits: tuple[WebSearchHit, ...]


def _snippet_text(html: str) -> str:
    """Reduce a MediaWiki search snippet (highlighted HTML) to plain text."""
    return " ".join(BeautifulSoup(html, "html.parser").get_text().split())


@dataclass(slots=True, frozen=True)
class WebSearch(RedirectingTool[Batch[WebSearchResults]]):
    """Search Wikipedia for up-to-date information.

    Queries the official MediaWiki API through the egress proxy with no
    scraping, bot detection, or search-engine rate limits.
    It only returns ``wikipedia.org`` links.
    The model picks the edition per call, ``default_edition`` when it names
    none.  The argument's pattern confines it to a subdomain label, and the
    client checks the resulting host against its URL policy like every request
    and redirect.

    ``client`` is the pooled, policy-checked web client; it is owned by the
    application lifespan, so this tool uses it without ever closing it.
    """

    injectable: ClassVar[bool] = True
    """The sandbox has no network, so a program can only be handed this."""

    client: httpx2.AsyncClient
    default_edition: str = "en"
    timeout_seconds: float = 10.0
    user_agent: str = field(default_factory=build_user_agent)

    @override
    async def __call__(
        self,
        searches: WebSearchesArg,
        max_results: WebMaxResultsArg = 5,
        output_path: OutputPathArg = None,
    ) -> ToolOutput[Batch[WebSearchResults] | RedirectedOutput]:
        """Search Wikipedia (and only Wikipedia) for up-to-date information.

        This searches the Wikipedia encyclopedia exclusively, not the
        open web: every result is a Wikipedia article, so it is the tool
        for encyclopedic facts, definitions, and background, but it
        cannot find news, forums, product pages, or any other site.
        Every language edition is searchable, and editions differ in
        coverage, so search the edition most likely to cover the topic.

        Returns each search's results with ``title``, ``href``, and
        ``body`` fields, an article already found by an earlier search of
        the call left out of the later ones. ``body`` is only a short
        snippet, so follow up with ``web_fetch`` on a result's ``href`` to
        read the full article.
        """
        keys = list(
            dict.fromkeys(
                (search.query, search.edition or self.default_edition)
                for search in searches
            )
        )

        async def hits(key: tuple[str, str]) -> list[WebSearchHit] | ToolRetry:
            try:
                return await self._hits(*key, max_results)
            except ToolRetry as exc:
                return exc

        # Fetched together, then rendered in request order, so which search
        # an article is listed under does not depend on which answered first.
        fetched = await bounded_gather(keys, hits, limit=_WEB_CONCURRENCY)
        found = dict(zip(keys, fetched, strict=True))
        seen: set[str] = set()

        async def render(
            key: tuple[str, str], _share: BatchShare
        ) -> ToolOutput[WebSearchResults]:
            outcome = found[key]

            if isinstance(outcome, ToolRetry):
                raise outcome

            fresh = tuple(hit for hit in outcome if hit.href not in seen)
            seen.update(hit.href for hit in fresh)
            results = WebSearchResults(*key, hits=fresh)

            return _search_output(results, repeated=len(outcome) - len(fresh))

        result = await run_batch(keys, render, key=lambda key: f"{key[0]} ({key[1]})")

        return await self.redirect(result, output_path)

    async def _hits(
        self, query: str, edition: str, max_results: int
    ) -> list[WebSearchHit]:
        """Run one search against the edition's MediaWiki API."""
        base_url = f"https://{edition}.wikipedia.org"
        endpoint = f"{base_url}/w/api.php"
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
                f"web search failed — the `{edition}` Wikipedia edition may not "
                "exist or be unavailable, or the query too narrow; check the "
                "edition code, try again, or rephrase the query."
            ) from exc

        return [
            WebSearchHit(
                title=hit.get("title", ""),
                href=f"{base_url}/wiki/"
                + quote(hit.get("title", "").replace(" ", "_")),
                body=_snippet_text(hit.get("snippet", "")),
            )
            for hit in hits
        ]


def _search_output(
    results: WebSearchResults, *, repeated: int
) -> ToolOutput[WebSearchResults]:
    """Render one search's articles as numbered blocks.

    *repeated* counts the articles left out as already listed by an earlier
    search, which is said so an empty block is not read as an empty search.
    """
    blocks: list[str] = []

    for i, hit in enumerate(results.hits, 1):
        block = f"[{i}] {hit.title} ({hit.href})"

        if hit.body:
            block += f"\n    {hit.body}"

        blocks.append(block)

    note = f"{repeated} {pluralize(repeated, 'article')} already listed above"

    if not blocks:
        formatted = f"(only {note})" if repeated else "(no results)"
    else:
        formatted = BLOCK_SEP.join(blocks) + hint_suffix([note] if repeated else [])

    return ToolOutput(data=results, formatted=formatted)


@dataclass(slots=True, frozen=True)
class WebPage:
    """Readable content extracted from a fetched web page.

    ``requested_url`` is the URL asked for and ``url`` the final one after
    redirects.  ``content`` is the page reduced to markdown (for HTML) or its
    raw text (for plain-text and JSON responses).
    """

    requested_url: str
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
class WebFetch(RedirectingTool[Batch[WebPage]]):
    """Fetch a web page and return its readable content.

    ``max_response_bytes`` caps how many raw bytes are downloaded per
    page and ``max_chars`` caps the extracted text.  ``max_line_chars``
    truncates each numbered line so a data-URI or minified line cannot
    flood the context, and ``max_formatted_chars`` bounds the rendered
    output as a whole, which neither of the other two does.  A call fetching
    several pages splits ``max_chars`` and ``max_formatted_chars`` between
    them, and keeps at most a few requests in flight.
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
        self, urls: WebUrlsArg, output_path: OutputPathArg = None
    ) -> ToolOutput[Batch[WebPage] | RedirectedOutput]:
        """Fetch one or more web pages as readable text.

        HTML is reduced to its markdown text content; plain-text and JSON
        responses pass through unchanged.  Each content line is numbered
        so it can be cited like a document line.  Redirects are followed
        by the client, whose request hook checks every hop against the URL
        host policy before the egress proxy connects.  Each page is shown
        under the URL it was asked for, and a URL redirecting to a page
        already fetched is not shown twice.
        """
        result = await run_batch(
            urls,
            self._fetch_or_retry,
            key=lambda url: url,
            concurrency=_WEB_CONCURRENCY,
            identity=lambda page: page.url,
        )

        return await self.redirect(result, output_path)

    async def _fetch_or_retry(
        self, url: str, share: BatchShare
    ) -> ToolOutput[WebPage]:
        """Fetch one page, turning every failure into a correctable refusal."""
        try:
            return await self._fetch(url, share)
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

    async def _fetch(self, url: str, share: BatchShare) -> ToolOutput[WebPage]:
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

        return self._finalize(
            url, str(response.url), response, mime, body, truncated, share
        )

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
        requested_url: str,
        url: str,
        response: httpx2.Response,
        mime: str,
        body: bytes,
        truncated: bool,
        share: BatchShare,
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

        max_chars = share.of(self.max_chars)

        if len(content) > max_chars:
            content = content[:max_chars]
            truncated = True

        page = WebPage(
            requested_url=requested_url,
            url=url,
            title=title,
            content=content,
            truncated=truncated,
        )

        if not content:
            return ToolOutput(data=page, formatted="(no readable text on this page)")
        header = f"{title} — {url}" if title else url
        rendered, omitted = cap_lines(
            iter_annotated(content.splitlines(), 1, self.max_line_chars),
            share.of(self.max_formatted_chars),
        )
        suffix = hint_suffix(["truncated"] if truncated or omitted else [])
        return ToolOutput(data=page, formatted=f"{header}\n{rendered}{suffix}")
