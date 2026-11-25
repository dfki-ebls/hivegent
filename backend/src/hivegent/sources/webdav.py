"""The built-in connector, to WebDAV servers such as Nextcloud.

The files are listed with `PROPFIND` one collection per request, a few at once, since servers commonly refuse `Depth: infinity`,
and named by their `oc:fileid` where the server has one, so a rename is a move.
A server answering the RFC 6578 `sync-collection` report, as Nextcloud does,
lists only what changed since the last sync token.
The connector only reads, and like rclone it lists what it can:
a folder, a response, or a file's properties the server refuses is named in the delta's `refused`,
which leaves it incomplete, so the sync mirrors the rest but reads nothing missing as a deletion,
while a root the server refuses or never answers fails the listing.
A first `sync-collection` report the server truncates (507) is completed by the walk.
Responses are parsed with document types refused, so no entity is ever expanded,
and only hrefs strictly below the root without dot segments are trusted,
so a hostile server can neither escape the root nor make the listing loop,
and a folder deeper than `max_depth` fails the listing instead of growing it without end.
A file is streamed and refused once it exceeds `max_file_size_bytes`.
"""

import logging
from collections.abc import Iterator
from dataclasses import dataclass, field, replace
from email.utils import parsedate_to_datetime
from http import HTTPStatus
from pathlib import PurePosixPath
from typing import Literal
from urllib.parse import quote
from xml.etree.ElementTree import Element, TreeBuilder, XMLParser
from xml.sax.saxutils import escape

import httpx2
from exar.helpers import bounded_gather

from ..plugins.api import SourceHost
from ..shared.connectors import Connector, ConnectorOptions, Delta, RemoteItem

__all__ = ["WebDAV", "webdav"]

logger = logging.getLogger(__name__)

_WALK_CONCURRENCY = 4

_DAV = "{DAV:}"
_OC = "{http://owncloud.org/ns}"
_NAMESPACES = 'xmlns:d="DAV:" xmlns:oc="http://owncloud.org/ns"'
_PROPS = (
    "<d:prop><d:resourcetype/><d:getetag/><d:getlastmodified/><oc:fileid/></d:prop>"
)
_PROPFIND = f'<?xml version="1.0"?><d:propfind {_NAMESPACES}>{_PROPS}</d:propfind>'


class _NoDoctype(TreeBuilder):
    def doctype(self, name: str, pubid: str | None, system: str | None) -> None:
        raise ValueError("Refusing an XML response with a document type")


def _parse_xml(body: bytes) -> Element:
    """Parses an XML response, refusing a document type and with it every entity.

    Raises:
        ValueError: For a document type.
        xml.etree.ElementTree.ParseError: For malformed XML.

    Examples:
        >>> _parse_xml(b'<d:multistatus xmlns:d="DAV:"/>').tag
        '{DAV:}multistatus'
    """
    parser = XMLParser(target=_NoDoctype())
    parser.feed(body)

    return parser.close()


def _parse_status(line: str | None) -> HTTPStatus | None:
    """Parses the status of a status line such as `HTTP/1.1 404 Not Found`, None if it has none.

    Examples:
        >>> _parse_status("HTTP/1.1 404 Not Found")
        <HTTPStatus.NOT_FOUND: 404>
        >>> _parse_status("garbage") is None
        True
    """
    match (line or "").split(maxsplit=2):
        case [version, code, *_] if version.startswith("HTTP/") and code.isdecimal():
            try:
                return HTTPStatus(int(code))
            except ValueError:
                return None
        case _:
            return None


@dataclass(frozen=True, slots=True)
class _Entry:
    """One response of a multistatus, a file, a collection, a path that is gone, or one refused."""

    path: str
    item: RemoteItem | None
    collection: bool = False
    refused: bool = False


class WebDAVOptions(ConnectorOptions):
    """The options of a WebDAV source."""


@dataclass(frozen=True, slots=True, kw_only=True)
class WebDAV:
    """The files below a URL of a WebDAV server.

    Args:
        url: The root of the source.
        client: The client of the requests, authenticated and owned by the caller.
        sync_collection: Whether to ask for changes with `sync-collection`,
            disable it for servers that refuse it to save a request per delta.
        options: The depth and file size caps.
    """

    url: str
    client: httpx2.AsyncClient
    sync_collection: bool = True
    options: WebDAVOptions = field(default_factory=WebDAVOptions)

    @property
    def _root(self) -> PurePosixPath:
        return PurePosixPath(httpx2.URL(self.url).path)

    def _url(self, path: str, collection: bool = False) -> httpx2.URL:
        target = self._root / path if path else self._root

        return httpx2.URL(self.url).join(
            quote(f"{target}/" if collection else str(target))
        )

    async def _multistatus(
        self,
        method: Literal["PROPFIND", "REPORT"],
        url: httpx2.URL,
        body: str,
        depth: Literal["0", "1"],
    ) -> httpx2.Response:
        return await self.client.request(
            method,
            url,
            content=body.encode(),
            headers={"Depth": depth, "Content-Type": "application/xml; charset=utf-8"},
        )

    def _entries(self, root: Element) -> Iterator[_Entry]:
        """The responses below the root, a path gone where the server says so.

        A response, or a file's properties, the server refuses is a refused
        entry, since skipping it would read as a deletion.
        """
        for response in root.iter(f"{_DAV}response"):
            href = PurePosixPath(httpx2.URL(response.findtext(f"{_DAV}href", "")).path)

            if (
                href == self._root
                or not href.is_relative_to(self._root)
                or ".." in href.parts
            ):
                continue

            path = href.relative_to(self._root).as_posix()

            match _parse_status(response.findtext(f"{_DAV}status")):
                case HTTPStatus.NOT_FOUND:
                    yield _Entry(path, None)
                    continue
                case None:
                    pass
                case status:
                    logger.warning("%s could not be listed: %s", path, status)
                    yield _Entry(path, None, refused=True)
                    continue

            # Another propstat only names the properties a server lacks, such as oc:fileid.
            prop = next(
                (
                    propstat.find(f"{_DAV}prop")
                    for propstat in response.iter(f"{_DAV}propstat")
                    if _parse_status(propstat.findtext(f"{_DAV}status"))
                    is HTTPStatus.OK
                ),
                None,
            )

            if prop is None:
                logger.warning("%s could not be listed: no properties", path)
                yield _Entry(path, None, refused=True)
                continue

            if prop.find(f"{_DAV}resourcetype/{_DAV}collection") is not None:
                yield _Entry(path, None, collection=True)
                continue

            modified = prop.findtext(f"{_DAV}getlastmodified")
            item = RemoteItem(
                id=prop.findtext(f"{_OC}fileid") or path,
                path=path,
                version=prop.findtext(f"{_DAV}getetag", ""),
                modified=parsedate_to_datetime(modified) if modified else None,
            )
            yield _Entry(path, item)

    async def _list(self, folder: str) -> httpx2.Response | httpx2.HTTPError:
        """The listing of *folder*, or the failure of the server to give one."""
        try:
            response = await self._multistatus(
                "PROPFIND", self._url(folder, collection=True), _PROPFIND, "1"
            )

            return response.raise_for_status()
        except httpx2.HTTPError as exc:
            return exc

    async def walk(self) -> Delta:
        """Every file below the root, one level of folders after another, each listed once.

        The folders of a level are listed at most :data:`_WALK_CONCURRENCY` at a time.
        A folder the server refuses or never answers is named as refused,
        which leaves the listing incomplete.

        Raises:
            httpx2.HTTPError: If the server refuses or never answers the root.
            ValueError: If a folder lies deeper than `max_depth`.
        """
        level, visited = [""], {""}
        items: list[RemoteItem] = []
        refused: list[str] = []

        while level:
            listings = await bounded_gather(level, self._list, limit=_WALK_CONCURRENCY)
            children: list[str] = []

            for folder, listing in zip(level, listings, strict=True):
                if isinstance(listing, httpx2.HTTPError):
                    if not folder:
                        raise listing

                    logger.warning("%s could not be listed: %s", folder, listing)
                    refused.append(folder)
                    continue

                for entry in self._entries(_parse_xml(listing.content)):
                    if entry.refused:
                        refused.append(entry.path)
                    elif entry.item is not None:
                        items.append(entry.item)
                    elif entry.collection and entry.path not in visited:
                        self.options.check_depth(entry.path)
                        visited.add(entry.path)
                        children.append(entry.path)

            level = sorted(children)

        return Delta(items=items, listed=True, refused=refused)

    async def read(self, item: RemoteItem, /) -> bytes:
        """The content of a file, streamed up to `max_file_size_bytes`.

        Raises:
            ValueError: If the file exceeds `max_file_size_bytes`.
        """
        async with self.client.stream("GET", self._url(item.path)) as response:
            response.raise_for_status()

            return await self.options.read_response(response)

    async def delta(self, cursor: str | None, /) -> Delta:
        """What changed since a sync token, every file for None, an expired token, or a refused report."""
        if not self.sync_collection:
            return await self.walk()

        body = (
            f'<?xml version="1.0"?><d:sync-collection {_NAMESPACES}>'
            f"<d:sync-token>{escape(cursor or '')}</d:sync-token>"
            f"<d:sync-level>infinite</d:sync-level>{_PROPS}</d:sync-collection>"
        )
        response = await self._multistatus(
            "REPORT", self._url("", collection=True), body, "0"
        )

        if response.status_code != HTTPStatus.MULTI_STATUS:
            if cursor is not None and _expired(response):
                return await self.delta(None)

            if cursor is not None:
                response.raise_for_status()

            return await self.walk()

        root = _parse_xml(response.content)
        token = root.findtext(f"{_DAV}sync-token")

        # The rest of a truncated report follows from its token, which only a
        # first report cannot wait for, since it has to list every file.
        if cursor is None and _truncated(root):
            return replace(await self.walk(), cursor=token)

        entries = list(self._entries(root))
        refused = [entry.path for entry in entries if entry.refused]

        return Delta(
            items=[entry.item for entry in entries if entry.item is not None],
            removed=[
                entry.path
                for entry in entries
                if entry.item is None and not entry.collection and not entry.refused
            ],
            cursor=token,
            listed=cursor is None,
            refused=refused,
        )


def _expired(response: httpx2.Response) -> bool:
    """Whether a response refuses a sync token the server no longer knows."""
    try:
        return _parse_xml(response.content).find(f"{_DAV}valid-sync-token") is not None
    except ValueError, SyntaxError:
        return False


def _truncated(root: Element) -> bool:
    """Whether a `sync-collection` report says it holds only part of the changes (RFC 6578, 3.6)."""
    return any(
        _parse_status(response.findtext(f"{_DAV}status"))
        is HTTPStatus.INSUFFICIENT_STORAGE
        for response in root.iter(f"{_DAV}response")
    )


def webdav(host: SourceHost) -> Connector:
    """Builds the connector of a WebDAV source.

    Raises:
        ValueError: For options it refuses.
    """
    return WebDAV(
        url=str(host.url), client=host.http_client(), options=host.config(WebDAVOptions)
    )
