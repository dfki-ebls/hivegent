"""Web search and fetch tool callables."""

import asyncio
import logging
from abc import ABC, abstractmethod
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from email.utils import parseaddr
from functools import lru_cache
from importlib.metadata import metadata
from typing import Annotated, ClassVar, Literal, override

import httpx2
from lxml.html import HtmlElement
from markdownify import MarkdownConverter
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from trafilatura import bare_extraction, html2txt, load_html
from trafilatura.htmlprocessing import build_html_output
from trafilatura.settings import Document

from ..concurrency import bounded_gather
from ..converters.formats import is_textual_media_type
from ..humanize import format_bytes, pluralize
from ..security import UnsafeUrlError
from .base import (
    AsyncTool,
    Batch,
    BatchShare,
    FullLinesArg,
    ItemFailure,
    ToolOutput,
    ToolRetry,
    batch_field,
    run_batch,
)
from .formatting import BLOCK_SEP, LineClip, hint_suffix, iter_annotated

__all__ = [
    "KnownUrls",
    "OpenserpBackend",
    "SearchBackend",
    "SearchPage",
    "SearxngBackend",
    "WebCategoryArg",
    "WebFetch",
    "WebLanguageArg",
    "WebMaxResultsArg",
    "WebPage",
    "WebQuery",
    "WebQueryArg",
    "WebSearch",
    "WebSearchHit",
    "WebSearchResults",
    "WebSearchesArg",
    "WebTimeRange",
    "WebTimeRangeArg",
    "WebUrlsArg",
    "build_user_agent",
]

_WEB_CONCURRENCY = 4
"""Requests one batched web call keeps in flight, polite to a single host."""

logger = logging.getLogger(__name__)


type _WebResult = WebSearchResults | WebPage | ItemFailure
"""One entry of a ``web_search`` or ``web_fetch`` batch."""

_MARKDOWN = MarkdownConverter(
    heading_style="ATX",
    bullets="-",
    escape_asterisks=False,
    escape_underscores=False,
    escape_misc=False,
    bs4_options="lxml",
)
"""Renders extracted HTML as markdown, leaving the URLs in link text intact."""

_PUNCTUATION = "<>[]{}\"'`*.,;:!?"
"""What may enclose or follow a URL in free text."""


@lru_cache(maxsize=2**16)
def _url_key(url: str) -> str | None:
    """Normalize *url* so two spellings of one page compare equal.

    The scheme may be left out and the fragment is ignored, the host is
    lowercased and non-ASCII characters are percent-encoded.  ``None`` stands
    for a text that is no URL.
    """
    try:
        parsed = httpx2.URL(url)

        if not parsed.host:
            parsed = httpx2.URL(f"https://{url}")
    except httpx2.InvalidURL:
        return None

    return str(parsed.copy_with(scheme="https", fragment=None))


def _spelled_urls(text: str, marker: str) -> tuple[str, ...]:
    """The words of *text* holding *marker* that may be URLs.

    Each word counts without the punctuation around it, once also without
    parentheses and once keeping them, as some URLs end with one.
    """
    return tuple(
        {
            variant
            for word in text.split()
            if marker in word
            for variant in (word.strip(_PUNCTUATION), word.strip(_PUNCTUATION + "()"))
        }
    )


@dataclass(slots=True)
class KnownUrls:
    """The URLs a turn has seen from sources the model cannot write.

    ``web_fetch`` opens a URL only if the user wrote it or a web tool returned
    it, so an instruction planted on a page cannot make the model spell a URL
    that carries conversation data out.  Shared with subagents and programs.

    >>> known = KnownUrls()
    >>> known.add_text("Summarize de.wikipedia.org/wiki/Müller, please.")
    >>> "https://de.wikipedia.org/wiki/M%C3%BCller#intro" in known
    True
    >>> "https://de.wikipedia.org/wiki/M%C3%BCller?leak=1" in known
    False
    >>> "https://de.wikipedia.org/wiki/M" in known
    False
    """

    urls: set[str] = field(default_factory=set)

    def add(self, *urls: str) -> None:
        """Know *urls*, skipping a text that is no URL."""
        self.urls.update(key for url in urls if (key := _url_key(url)))

    def add_text(self, text: str) -> None:
        """Know every URL a free text such as a user prompt names, even without a scheme."""
        self.add(*_spelled_urls(text, "."))

    def add_results(self, results: Iterable[_WebResult]) -> None:
        """Know every URL the web tools returned in *results*."""
        for result in results:
            if not isinstance(result, ItemFailure):
                self.add(*result.urls)

    def __contains__(self, url: str) -> bool:
        return _url_key(url) in self.urls


def build_user_agent(contact: str = "") -> str:
    """Build a descriptive User-Agent identifying the app and an operator.

    Well-behaved hosts such as Wikipedia reject a generic library agent, so
    name the application and a contact address for traffic questions.
    *contact* is the operator email, falling back to the package author.
    """
    meta = metadata("hivegent-backend")
    contact = contact or parseaddr(meta.get("Author-email", ""))[1]
    suffix = f" (+mailto:{contact})" if contact else ""
    return f"{meta['Name']}/{meta['Version']}{suffix}"


WebTimeRange = Literal["day", "week", "month", "year"]

WebQueryArg = Annotated[
    str,
    Field(description="Search query string, phrased in the language of the results."),
]
WebCategoryArg = Annotated[
    Literal["general", "news", "science", "it"],
    Field(
        description=(
            "Kind of source to search: `news` for current events, `science` for "
            "papers, `it` for programming, and `general` for everything else."
        )
    ),
]
WebLanguageArg = Annotated[
    str | None,
    Field(
        description=(
            "Language of the results as a code like `en`, `de`, or `en-US`. "
            "Search another one (e.g. English or the user's language) when "
            "results are poor. Omit it for the search service's default."
        ),
        pattern=r"^[a-z]{2,3}(-[A-Z]{2})?$",
    ),
]
WebTimeRangeArg = Annotated[
    WebTimeRange | None,
    Field(
        description=(
            "Only find pages from the last day, week, month, or year, for recent "
            "events. Omit it to search all time."
        )
    ),
]
WebMaxResultsArg = Annotated[
    int,
    Field(description="Maximum number of results to return per search.", ge=1, le=20),
]
WebUrlsArg = Annotated[
    list[str],
    batch_field(
        "HTTP or HTTPS URLs to fetch. Fetch every page you need in one call, "
        "and a page that fails is reported without failing the others."
    ),
]


@dataclass(slots=True, frozen=True)
class WebQuery:
    """One search: the query, and the filters to run it with."""

    __pydantic_config__ = ConfigDict(extra="forbid")

    query: WebQueryArg
    category: WebCategoryArg = "general"
    language: WebLanguageArg = None
    time_range: WebTimeRangeArg = None


WebSearchesArg = Annotated[
    list[WebQuery],
    batch_field(
        "Searches to run. Search several phrasings, languages, or categories in "
        "one call rather than one call each, and a page several of them find is "
        "listed once."
    ),
]


@dataclass(slots=True, frozen=True)
class WebSearchHit:
    """One page a search found, with its snippet and publication date."""

    title: str
    href: str
    body: str
    published: str | None = None


@dataclass(slots=True, frozen=True)
class WebSearchResults:
    """The pages one search found that no earlier search of the call had."""

    query: str
    hits: tuple[WebSearchHit, ...]

    @property
    def urls(self) -> tuple[str, ...]:
        """The pages the model may fetch next."""
        return tuple(hit.href for hit in self.hits)


def _hit(
    url: str | None, title: str, body: str, published: str | None = None
) -> WebSearchHit | None:
    """Shape one result, ``None`` for one without a link the model could fetch."""
    if url is None or not url.startswith(("https://", "http://")):
        return None

    return WebSearchHit(
        title=" ".join(title.split()),
        href=url,
        body=" ".join(body.split()),
        published=published[:10] if published else None,
    )


@dataclass(slots=True, frozen=True)
class SearchPage:
    """What a backend answered one search with.

    ``failed`` describes the engines that did not answer, which explains a
    search that found nothing.
    """

    hits: tuple[WebSearchHit, ...]
    failed: tuple[str, ...] = ()


@dataclass(slots=True, frozen=True)
class SearchBackend(ABC):
    """A metasearch service ``web_search`` queries over its HTTP JSON API.

    A backend only translates: a search into a request to its ``url``, and the
    response body into a :class:`SearchPage`.  The tool owns the request, its
    errors, and everything done with the results.
    """

    path: ClassVar[str]
    base_url: str

    @property
    def url(self) -> str:
        """The endpoint a search is sent to."""
        return f"{self.base_url.rstrip('/')}{self.path}"

    @abstractmethod
    def params(self, search: WebQuery, max_results: int) -> dict[str, str | None]:
        """The query parameters that run *search*, ``None`` for one left out."""

    @abstractmethod
    def parse(self, content: bytes) -> SearchPage:
        """Read a response body, raising ``ValidationError`` on a foreign one."""


class _SearxngResult(BaseModel):
    url: str | None = None
    title: str = ""
    content: str = ""
    published_date: str | None = Field(default=None, alias="publishedDate")


class _SearxngResponse(BaseModel):
    results: tuple[_SearxngResult, ...] = ()
    unresponsive_engines: tuple[tuple[str, str], ...] = ()


@dataclass(slots=True, frozen=True)
class SearxngBackend(SearchBackend):
    """SearXNG, which must list ``json`` under its ``search.formats``.

    It honours every filter, and a search naming no language takes the
    instance's ``search.default_lang``.
    """

    path: ClassVar[str] = "/search"

    @override
    def params(self, search: WebQuery, max_results: int) -> dict[str, str | None]:
        return {
            "q": search.query,
            "format": "json",
            "categories": search.category,
            "language": search.language,
            "time_range": search.time_range,
        }

    @override
    def parse(self, content: bytes) -> SearchPage:
        payload = _SearxngResponse.model_validate_json(content)
        hits = (
            _hit(result.url, result.title, result.content, result.published_date)
            for result in payload.results
        )

        return SearchPage(
            hits=tuple(hit for hit in hits if hit),
            failed=tuple(
                f"{engine}: {error}" for engine, error in payload.unresponsive_engines
            ),
        )


class _OpenserpResult(BaseModel):
    url: str | None = None
    title: str = ""
    snippet: str = ""


class _OpenserpMeta(BaseModel):
    engines_failed: tuple[str, ...] = ()


class _OpenserpResponse(BaseModel):
    results: tuple[_OpenserpResult, ...] = ()
    meta: _OpenserpMeta = _OpenserpMeta()


_TIME_RANGE_DAYS: dict[WebTimeRange, int] = {
    "day": 1,
    "week": 7,
    "month": 31,
    "year": 365,
}


@dataclass(slots=True, frozen=True)
class OpenserpBackend(SearchBackend):
    """OpenSERP's megasearch, which merges the results of all its engines.

    It knows no categories, so every search covers the general web, and its
    results carry no publication date.  A language such as ``en-US`` becomes
    its ``lang`` and ``region``, and a time range the dates up to today.
    """

    path: ClassVar[str] = "/mega/search"

    @override
    def params(self, search: WebQuery, max_results: int) -> dict[str, str | None]:
        language, _, region = (search.language or "").partition("-")
        today = datetime.now(UTC).date()
        days = _TIME_RANGE_DAYS[search.time_range] if search.time_range else None
        since = (
            f"{today - timedelta(days=days):%Y%m%d}..{today:%Y%m%d}" if days else None
        )

        return {
            "text": search.query,
            "limit": str(max_results),
            "lang": language.upper() or None,
            "region": region or None,
            "date": since,
        }

    @override
    def parse(self, content: bytes) -> SearchPage:
        payload = _OpenserpResponse.model_validate_json(content)
        hits = (
            _hit(result.url, result.title, result.snippet) for result in payload.results
        )

        return SearchPage(
            hits=tuple(hit for hit in hits if hit),
            failed=payload.meta.engines_failed,
        )


def _label(search: WebQuery) -> str:
    """The query with the filters that tell it apart from another search."""
    category = search.category if search.category != "general" else None
    filters = ", ".join(
        value for value in (category, search.language, search.time_range) if value
    )

    return f"{search.query} [{filters}]" if filters else search.query


@dataclass(slots=True, frozen=True)
class WebSearch(AsyncTool[Batch[WebSearchResults]]):
    """Search the web through a metasearch service.

    The ``backend`` sends each query to several search engines and merges
    their rankings, so no API key is needed here.
    Its API is an operator endpoint, reached with the trusted ``client``, and
    the model only ever chooses the query, never a host the service connects
    to.
    The results link anywhere, and the web client's URL policy decides which of
    them ``web_fetch`` may open.
    ``timeout_seconds`` bounds each search as a whole.

    ``client`` is owned by the application lifespan, so this tool uses it
    without ever closing it.
    """

    injectable: ClassVar[bool] = True
    """The sandbox has no network, so a program can only be handed this."""

    client: httpx2.AsyncClient
    backend: SearchBackend
    known_urls: KnownUrls
    timeout_seconds: float

    @override
    async def __call__(
        self,
        searches: WebSearchesArg,
        max_results: WebMaxResultsArg = 5,
    ) -> ToolOutput[Batch[WebSearchResults]]:
        """Search the web for up-to-date information.

        Every search runs through several search engines at once, so it
        finds news, documentation, papers, forums, and any other public
        page.  Pick the category, language, and time range that fit the
        question, e.g. the `news` category and a recent time range for
        current events.

        Returns each search's results with ``title``, ``href``, ``body``,
        and ``published`` fields, a page already found by an earlier search
        of the call left out of the later ones. ``body`` is only a short
        snippet of the page.
        """
        unique = list(dict.fromkeys(searches))

        async def hits(search: WebQuery) -> tuple[WebSearchHit, ...] | ToolRetry:
            try:
                return await self._hits(search, max_results)
            except ToolRetry as exc:
                return exc

        # Fetched together, then rendered in request order, so which search
        # a page is listed under does not depend on which answered first.
        fetched = await bounded_gather(unique, hits, limit=_WEB_CONCURRENCY)
        found = dict(zip(unique, fetched, strict=True))
        seen: set[str] = set()

        async def render(
            search: WebQuery, _share: BatchShare
        ) -> ToolOutput[WebSearchResults]:
            outcome = found[search]

            if isinstance(outcome, ToolRetry):
                raise outcome

            fresh = tuple(hit for hit in outcome if hit.href not in seen)
            seen.update(hit.href for hit in fresh)
            results = WebSearchResults(search.query, fresh)

            return _search_output(results, repeated=len(outcome) - len(fresh))

        output = await run_batch(unique, render, key=_label)
        self.known_urls.add_results(output.data)

        return output

    async def _hits(
        self, search: WebQuery, max_results: int
    ) -> tuple[WebSearchHit, ...]:
        """Run one search against the backend."""
        params = self.backend.params(search, max_results)

        try:
            async with asyncio.timeout(self.timeout_seconds):
                response = await self.client.get(
                    self.backend.url,
                    params={
                        key: value for key, value in params.items() if value is not None
                    },
                )

            response.raise_for_status()
            page = self.backend.parse(response.content)
        except (TimeoutError, httpx2.HTTPError, ValidationError) as exc:
            logger.warning("Web search failed for query %r: %s", search.query, exc)
            raise ToolRetry(
                "web search failed, the language code may be unknown or the "
                "search service unavailable; check the arguments and try again."
            ) from exc

        if not page.hits and page.failed:
            raise ToolRetry(
                f"no search engine answered ({', '.join(page.failed)}); try again "
                "later or rephrase the query."
            )

        return page.hits[:max_results]


def _search_output(
    results: WebSearchResults, *, repeated: int
) -> ToolOutput[WebSearchResults]:
    """Render one search's pages as numbered blocks.

    *repeated* counts the pages left out as already listed by an earlier
    search, which is said so an empty block is not read as an empty search.
    """
    blocks: list[str] = []

    for i, hit in enumerate(results.hits, 1):
        block = f"[{i}] {hit.title} ({hit.href})"

        if hit.published:
            block += f"\n    published {hit.published}"

        if hit.body:
            block += f"\n    {hit.body}"

        blocks.append(block)

    note = f"{repeated} {pluralize(repeated, 'page')} already listed above"

    if not blocks:
        formatted = f"(only {note})" if repeated else "(no results)"
    else:
        formatted = BLOCK_SEP.join(blocks) + hint_suffix([note] if repeated else [])

    return ToolOutput(data=results, formatted=formatted)


@dataclass(slots=True, frozen=True)
class WebPage:
    """Readable content extracted from a fetched web page.

    ``requested_url`` is the URL asked for and ``url`` the final one after
    redirects.  ``content`` is the page's main content as markdown (for HTML)
    or its raw text (for plain-text and JSON responses), ``published`` the date the page states it was published, if
    any, and ``links`` the absolute targets of all its links or the URLs its
    text spells out.
    """

    requested_url: str
    url: str
    title: str
    content: str
    truncated: bool
    published: str | None = None
    links: tuple[str, ...] = ()

    @property
    def urls(self) -> tuple[str, ...]:
        """The pages the model may fetch next."""
        return (self.url, *self.links)


def _mime_type(content_type: str) -> str:
    """Return the bare lowercase mime type from a Content-Type header."""
    return content_type.split(";")[0].strip().lower()


def _is_html(mime: str) -> bool:
    return mime in ("text/html", "application/xhtml+xml")


def _inline_links(tree: HtmlElement, url: str) -> tuple[str, ...]:
    """Write each link of *tree* as the text ``[text](url)`` and return the targets.

    Every extractor keeps text while some drop link elements, so this is what
    lets the model follow a link of any page.  Targets are made absolute
    against *url*, and links within the page or to other schemes are left out.
    """
    page = httpx2.URL(url).copy_with(fragment=None)
    links: dict[str, None] = {}

    for anchor in list(tree.iter("a")):
        try:
            target = page.join(anchor.get("href", ""))
        except httpx2.InvalidURL:
            continue

        if (
            target.scheme not in ("http", "https")
            or target.copy_with(fragment=None) == page
        ):
            continue

        link = str(target)
        links[link] = None
        text = " ".join("".join(anchor.itertext()).split())
        anchor.clear(keep_tail=True)
        anchor.text = f"[{text or link}]({link})"

    return tuple(links)


def _extract_html(
    body: bytes, url: str, *, fast: bool
) -> tuple[str, str, str | None, tuple[str, ...]]:
    """Extract ``(title, markdown, published, links)`` from raw HTML bytes.

    trafilatura sniffs the encoding, keeps the main content as HTML, and drops
    navigation, boilerplate, and reader comments, so the model reads the
    article rather than the page around it, which is then rendered as
    markdown with the links :func:`_inline_links` wrote.  A page without main
    content falls back to all of its text.  *fast* skips the backup extractors trafilatura compares
    its own result with.
    """
    tree = load_html(body)

    if tree is None:
        return "", "", None, ()

    links = _inline_links(tree, url)

    document = bare_extraction(
        tree,
        url=url,
        fast=fast,
        include_comments=False,
        include_formatting=True,
        with_metadata=True,
    )

    if not isinstance(document, Document) or not document.text:
        return "", html2txt(tree).strip(), None, links

    markdown = _MARKDOWN.convert(build_html_output(document)).strip()

    return document.title or "", markdown, document.date, links


@dataclass(slots=True, frozen=True)
class WebFetch(AsyncTool[Batch[WebPage]]):
    """Fetch a web page and return its readable content.

    ``timeout_seconds`` bounds each page download as a whole and
    ``max_response_bytes`` caps how many raw bytes are downloaded per
    page.  ``max_line_chars`` truncates each numbered line so a data-URI or
    minified line cannot flood the context, unless the call passes
    ``full_lines``, and ``fast_extraction`` trades some recall for less CPU
    per page.  A call keeps at most a few requests in flight.
    Only a URL in ``known_urls`` is fetched, and every page a fetched page
    links to joins it.
    ``client`` is the pooled web client: its request hook validates the URL and
    every redirect hop against the URL host policy, and the egress proxy rejects
    non-public destinations after resolution.  Following redirects and the hop
    limit are both client-level in HTTPX, so they are configured there.  The
    lifespan owns the client, so this tool uses it without ever closing it.
    """

    injectable: ClassVar[bool] = True
    """The sandbox has no network, so a program can only be handed this."""

    client: httpx2.AsyncClient
    known_urls: KnownUrls
    timeout_seconds: float
    max_response_bytes: int = 5_000_000
    max_line_chars: int = 2000
    fast_extraction: bool = False
    user_agent: str = field(default_factory=build_user_agent)

    @override
    async def __call__(
        self, urls: WebUrlsArg, full_lines: FullLinesArg = False
    ) -> ToolOutput[Batch[WebPage]]:
        """Fetch one or more web pages as readable text.

        Only a URL that appeared in the conversation can be fetched: one the
        user wrote, a search result, or a link on a fetched page.
        HTML is reduced to its main content as markdown, without navigation,
        ads, or comments.  Plain-text and JSON responses pass through
        unchanged.  Each content line is numbered
        so it can be cited like a document line.  Redirects are followed
        by the client, whose request hook checks every hop against the URL
        host policy before the egress proxy connects.  Each page is shown
        under the URL it was asked for, and a URL redirecting to a page
        already fetched is not shown twice.
        """

        async def fetch(url: str, _share: BatchShare) -> ToolOutput[WebPage]:
            return await self._fetch_or_retry(url, full_lines)

        output = await run_batch(
            urls,
            fetch,
            key=lambda url: url,
            concurrency=_WEB_CONCURRENCY,
            identity=lambda page: page.url,
        )
        self.known_urls.add_results(output.data)

        return output

    async def _fetch_or_retry(self, url: str, full_lines: bool) -> ToolOutput[WebPage]:
        """Fetch one page, turning every failure into a correctable refusal."""
        try:
            return await self._fetch(url, full_lines)
        except ToolRetry:
            raise
        except (TimeoutError, httpx2.TimeoutException) as exc:
            raise ToolRetry("request timed out.") from exc
        except httpx2.HTTPStatusError as exc:
            raise ToolRetry(f"HTTP {exc.response.status_code}.") from exc
        except (UnsafeUrlError, httpx2.RequestError) as exc:
            raise ToolRetry(str(exc) or "request failed.") from exc
        except Exception as exc:
            logger.exception("Web fetch failed for URL %r", url)
            raise ToolRetry("failed to fetch URL.") from exc

    async def _fetch(self, url: str, full_lines: bool) -> ToolOutput[WebPage]:
        if url not in self.known_urls:
            raise ToolRetry(
                "this URL has not appeared in the conversation; fetch only URLs "
                "the user gave, search results, or links on fetched pages."
            )

        async with (
            asyncio.timeout(self.timeout_seconds),
            self.client.stream(
                "GET", url, headers={"User-Agent": self.user_agent}
            ) as response,
        ):
            response.raise_for_status()
            mime = _mime_type(response.headers.get("content-type", ""))

            if not is_textual_media_type(mime):
                raise ToolRetry(f"unsupported content type '{mime}'.")

            body, truncated = await self._read_capped(response)

        # Parsing a multi-megabyte page is CPU-bound, so it must not stall the
        # event loop every other request of the server shares.
        return await asyncio.to_thread(
            self._finalize, url, response, mime, body, truncated, full_lines
        )

    async def _read_capped(self, response: httpx2.Response) -> tuple[bytes, bool]:
        """Stream the response body up to the configured byte cap.

        HTTPX decompresses in pieces of at most a mebibyte, so a compression
        bomb overshoots the cap by no more than one piece.
        """
        buffer = bytearray()

        async for chunk in response.aiter_bytes():
            buffer += chunk

            if len(buffer) > self.max_response_bytes:
                return bytes(buffer[: self.max_response_bytes]), True

        return bytes(buffer), False

    def _finalize(
        self,
        requested_url: str,
        response: httpx2.Response,
        mime: str,
        body: bytes,
        truncated: bool,
        full_lines: bool,
    ) -> ToolOutput[WebPage]:
        url = str(response.url)
        if _is_html(mime):
            title, content, published, links = _extract_html(
                body, url, fast=self.fast_extraction
            )
        else:
            title, published = "", None
            content = body.decode(
                response.encoding or "utf-8", errors="replace"
            ).strip()
            links = _spelled_urls(content, "://")

        page = WebPage(
            requested_url=requested_url,
            url=url,
            title=title,
            content=content,
            truncated=truncated,
            published=published,
            links=links,
        )

        if not content:
            return ToolOutput(data=page, formatted="(no readable text on this page)")

        header = f"{title} ({url})" if title else url

        if published:
            header += f", published {published}"

        clip = LineClip(None if full_lines else self.max_line_chars)
        rendered = "\n".join(iter_annotated(content.splitlines(), 1, clip))
        hints = clip.hints()

        if truncated:
            cap = format_bytes(self.max_response_bytes)
            hints.append(f"page cut at the {cap} download cap")

        suffix = hint_suffix(hints)

        return ToolOutput(data=page, formatted=f"{header}\n{rendered}{suffix}")
