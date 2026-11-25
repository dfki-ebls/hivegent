"""The built-in WebDAV connector against a mocked Nextcloud."""

import httpx2
import pytest

from hivegent.shared.connectors import RemoteItem
from hivegent.sources.webdav import WebDAV, WebDAVOptions

ROOT = "/dav/files/u%20ser"


def response(
    href: str,
    status: str = "HTTP/1.1 200 OK",
    collection: bool = False,
    etag: str = "e1",
) -> str:
    kind = "<d:collection/>" if collection else ""

    return (
        f"<d:response><d:href>{href}</d:href><d:propstat><d:prop>"
        f"<d:resourcetype>{kind}</d:resourcetype><d:getetag>{etag}</d:getetag>"
        f"<oc:fileid>{href.rsplit('/', 1)[-1] or 'root'}</oc:fileid>"
        f"</d:prop><d:status>{status}</d:status></d:propstat></d:response>"
    )


def multistatus(*responses: str, token: str = "") -> bytes:
    sync = f"<d:sync-token>{token}</d:sync-token>" if token else ""

    return (
        f'<d:multistatus xmlns:d="DAV:" xmlns:oc="http://owncloud.org/ns">{"".join(responses)}{sync}</d:multistatus>'
    ).encode()


def handler(request: httpx2.Request) -> httpx2.Response:
    match request.method, request.url.path:
        case "PROPFIND", "/dav/files/u ser/":
            body = multistatus(
                response(f"{ROOT}/", collection=True),
                response(f"{ROOT}/a.md"),
                response(f"{ROOT}/sub/", collection=True),
            )
        case "PROPFIND", "/dav/files/u ser/sub/":
            body = multistatus(
                response(f"{ROOT}/sub/", collection=True), response(f"{ROOT}/sub/b.md")
            )
        case "REPORT", _:
            body = multistatus(
                response(f"{ROOT}/a.md", etag="e2"),
                f"<d:response><d:href>{ROOT}/gone.md</d:href><d:status>HTTP/1.1 404 Not Found</d:status></d:response>",
                token="t2",
            )
        case _:
            return httpx2.Response(500)

    return httpx2.Response(207, content=body)


async def test_webdav() -> None:
    async with httpx2.AsyncClient(transport=httpx2.MockTransport(handler)) as client:
        source = WebDAV(url=f"https://cloud.example{ROOT}/", client=client)

        listing = await source.walk()

        assert sorted(item.path for item in listing.items) == ["a.md", "sub/b.md"]
        assert (listing.complete, listing.refused) == (True, [])

        delta = await source.delta("t1")

        assert delta.items == [RemoteItem(id="a.md", path="a.md", version="e2")]
        assert (delta.removed, delta.cursor, delta.complete) == (
            ["gone.md"],
            "t2",
            False,
        )


async def test_webdav_incomplete_listing() -> None:
    """A refused propstat or folder leaves the walk incomplete, and a truncated first report is completed by it."""
    refused = False

    def partial(request: httpx2.Request) -> httpx2.Response:
        match request.method:
            case "REPORT":
                status = "HTTP/1.1 507 Insufficient Storage"
                body = multistatus(
                    f"<d:response><d:href>{ROOT}/</d:href><d:status>{status}</d:status></d:response>",
                    response(f"{ROOT}/a.md"),
                    token="t1",
                )
            case "PROPFIND" if request.url.path.endswith("/sub/"):
                return httpx2.Response(403)
            case _:
                body = multistatus(
                    response(f"{ROOT}/a.md"),
                    response(f"{ROOT}/b.md", status="HTTP/1.1 403 Forbidden")
                    if refused
                    else response(f"{ROOT}/b.md"),
                    response(f"{ROOT}/sub/", collection=True),
                )

        return httpx2.Response(207, content=body)

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(partial)) as client:
        source = WebDAV(url=f"https://cloud.example{ROOT}/", client=client)
        delta = await source.delta(None)

        assert (len(delta.items), delta.cursor, delta.complete) == (2, "t1", False)
        assert delta.refused == ["sub"]

        refused = True
        listing = await source.walk()

        assert [item.path for item in listing.items] == ["a.md"]
        assert (listing.complete, sorted(listing.refused)) == (False, ["b.md", "sub"])


async def test_webdav_refused_root() -> None:
    """A root the server refuses fails the listing rather than read as empty."""
    transport = httpx2.MockTransport(lambda _: httpx2.Response(403))

    async with httpx2.AsyncClient(transport=transport) as client:
        source = WebDAV(url=f"https://cloud.example{ROOT}/", client=client)

        with pytest.raises(httpx2.HTTPStatusError):
            await source.walk()


async def test_webdav_hostile_listing() -> None:
    listed = list[str]()

    def hostile(request: httpx2.Request) -> httpx2.Response:
        listed.append(request.url.path)

        return httpx2.Response(
            207,
            content=multistatus(
                response(f"{ROOT}/", collection=True),
                response("/dav/files/", collection=True),
                response(f"{ROOT}/sub/../sub/", collection=True),
                response(f"{ROOT}/sub/", collection=True),
            ),
        )

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(hostile)) as client:
        source = WebDAV(url=f"https://cloud.example{ROOT}/", client=client)

        assert (await source.walk()).items == []
        assert listed == ["/dav/files/u ser/", "/dav/files/u ser/sub/"]


async def test_webdav_depth_cap() -> None:
    """A server inventing ever deeper folders fails the listing at the cap."""

    def endless(request: httpx2.Request) -> httpx2.Response:
        here = request.url.path.removeprefix("/dav/files/u ser")

        return httpx2.Response(
            207, content=multistatus(response(f"{ROOT}{here}deeper/", collection=True))
        )

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(endless)) as client:
        source = WebDAV(
            url=f"https://cloud.example{ROOT}/",
            client=client,
            options=WebDAVOptions(max_depth=3),
        )

        with pytest.raises(ValueError, match="max_depth 3"):
            await source.walk()


async def test_webdav_read_cap() -> None:
    transport = httpx2.MockTransport(lambda _: httpx2.Response(200, content=b"12345"))

    async with httpx2.AsyncClient(transport=transport) as client:
        url = f"https://cloud.example{ROOT}/"
        item = RemoteItem(id="a.md", path="a.md", version="e1")

        with pytest.raises(ValueError, match="exceeds"):
            await WebDAV(
                url=url, client=client, options=WebDAVOptions(max_file_size_bytes=4)
            ).read(item)

        source = WebDAV(
            url=url, client=client, options=WebDAVOptions(max_file_size_bytes=5)
        )

        assert await source.read(item) == b"12345"
