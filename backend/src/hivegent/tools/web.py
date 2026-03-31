"""Web search and fetch tool callables."""

import ipaddress
import logging
import socket
from dataclasses import dataclass
from typing import Annotated, override

import httpx
from ddgs import DDGS
from pydantic import Field

from .base import Tool

__all__ = [
    "WebFetch",
    "WebMaxResultsArg",
    "WebQueryArg",
    "WebSearch",
    "WebUrlArg",
]

logger = logging.getLogger(__name__)

_TIMEOUT_SECONDS = 10.0

_BLOCKED_SCHEMES = frozenset({"file", "ftp", "data", "javascript"})

_PRIVATE_NETWORKS = [
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("fc00::/7"),
    ipaddress.ip_network("fe80::/10"),
]

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


def _is_private_ip(host: str) -> bool:
    """Check if a hostname resolves to a private/reserved IP address."""
    try:
        infos = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
    except socket.gaierror:
        return True
    for info in infos:
        addr = ipaddress.ip_address(info[4][0])
        if addr.is_private or addr.is_reserved or addr.is_loopback:
            return True
        for network in _PRIVATE_NETWORKS:
            if addr in network:
                return True
    return False


def _validate_url(url: str) -> str | None:
    """Validate a URL for safety. Returns an error message or None if valid."""
    parsed = httpx.URL(url)
    scheme = str(parsed.scheme).lower()
    if scheme in _BLOCKED_SCHEMES or scheme not in ("http", "https"):
        return f"Error: URL scheme '{scheme}' is not allowed. Use http or https."
    host = str(parsed.host)
    if not host:
        return "Error: URL has no host."
    if _is_private_ip(host):
        return "Error: URL resolves to a private or reserved IP address."
    return None


@dataclass(slots=True, frozen=True)
class WebSearch(Tool):
    """Search the web using DuckDuckGo."""

    @override
    def __call__(
        self,
        query: WebQueryArg,
        max_results: WebMaxResultsArg = 5,
    ) -> list[dict[str, str]]:
        """Search the web using DuckDuckGo for up-to-date information.

        Returns a list of results with ``title``, ``href``, and ``body`` fields.
        """
        max_results = min(max(1, max_results), 20)
        try:
            with DDGS() as ddgs:
                results = list(ddgs.text(query, max_results=max_results))
            return [
                {
                    "title": r.get("title", ""),
                    "href": r.get("href", ""),
                    "body": r.get("body", ""),
                }
                for r in results
            ]
        except Exception:
            logger.exception("Web search failed for query %r", query)
            return []


@dataclass(slots=True, frozen=True)
class WebFetch(Tool):
    """Fetch web page content as plain text."""

    max_response_bytes: int = 1_000_000

    @override
    async def __call__(self, url: WebUrlArg) -> str:
        """Fetch the content of a web page as plain text.

        Follows redirects.
        Limited to 1 MB response size and 10 second timeout.
        """
        error = _validate_url(url)
        if error:
            return error

        try:
            async with httpx.AsyncClient(
                timeout=_TIMEOUT_SECONDS,
                follow_redirects=True,
                max_redirects=5,
            ) as client:
                response = await client.get(url)
                response.raise_for_status()

                content_type = response.headers.get("content-type", "")
                if (
                    "text/" not in content_type
                    and "application/json" not in content_type
                ):
                    return f"Error: unsupported content type '{content_type}'."

                body = response.text
                if len(body) > self.max_response_bytes:
                    body = body[: self.max_response_bytes] + "\n\n[truncated]"
                return body
        except httpx.TimeoutException:
            return "Error: request timed out."
        except httpx.HTTPStatusError as exc:
            return f"Error: HTTP {exc.response.status_code}."
        except Exception:
            logger.exception("Web fetch failed for URL %r", url)
            return "Error: failed to fetch URL."
