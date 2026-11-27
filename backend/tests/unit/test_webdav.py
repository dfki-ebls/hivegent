"""The built-in WebDAV connector against a mocked Nextcloud."""

from collections.abc import AsyncGenerator, Callable

import httpx2
import pytest

from hivegent.shared.connectors import RemoteItem
from hivegent.sources.webdav import WebDAV, WebDAVOptions

ROOT = "/dav/files/u%20ser"

Handler = Callable[[httpx2.Request], httpx2.Response]
Connect = Callable[..., WebDAV]


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


@pytest.fixture()
async def connect() -> AsyncGenerator[Connect]:
    """Build a WebDAV source whose server is a handler, closing its clients afterwards."""
    clients: list[httpx2.AsyncClient] = []

    def make(handler: Handler, **options: int) -> WebDAV:
        client = httpx2.AsyncClient(transport=httpx2.MockTransport(handler))
        clients.append(client)

        return WebDAV(
            url=f"https://cloud.example{ROOT}/",
            client=client,
            options=WebDAVOptions(**options),
        )

    yield make

    for client in clients:
        await client.aclose()


async def test_webdav(connect: Connect) -> None:
    source = connect(handler)

    listing = await source.walk()

    assert sorted(item.path for item in listing.items) == ["a.md", "sub/b.md"]
    assert (listing.complete, listing.refused) == (True, [])

    delta = await source.delta("t1")

    assert delta.items == [RemoteItem(id="a.md", path="a.md", version="e2")]
    assert (delta.removed, delta.cursor, delta.complete) == (["gone.md"], "t2", False)


async def test_webdav_incomplete_listing(connect: Connect) -> None:
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

    source = connect(partial)
    delta = await source.delta(None)

    assert (len(delta.items), delta.cursor, delta.complete) == (2, "t1", False)
    assert delta.refused == ["sub"]

    refused = True
    listing = await source.walk()

    assert [item.path for item in listing.items] == ["a.md"]
    assert (listing.complete, sorted(listing.refused)) == (False, ["b.md", "sub"])


async def test_webdav_refused_root(connect: Connect) -> None:
    """A root the server refuses fails the listing rather than read as empty."""
    with pytest.raises(httpx2.HTTPStatusError):
        await connect(lambda _: httpx2.Response(403)).walk()


async def test_webdav_hostile_listing(connect: Connect) -> None:
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

    assert (await connect(hostile).walk()).items == []
    assert listed == ["/dav/files/u ser/", "/dav/files/u ser/sub/"]


async def test_webdav_depth_cap(connect: Connect) -> None:
    """A server inventing ever deeper folders fails the listing at the cap."""

    def endless(request: httpx2.Request) -> httpx2.Response:
        here = request.url.path.removeprefix("/dav/files/u ser")

        return httpx2.Response(
            207, content=multistatus(response(f"{ROOT}{here}deeper/", collection=True))
        )

    with pytest.raises(ValueError, match="max_depth 3"):
        await connect(endless, max_depth=3).walk()


async def test_webdav_read_cap(connect: Connect) -> None:
    def served(_: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(200, content=b"12345")

    item = RemoteItem(id="a.md", path="a.md", version="e1")

    with pytest.raises(ValueError, match="exceeds"):
        await connect(served, max_file_size_bytes=4).read(item)

    assert await connect(served, max_file_size_bytes=5).read(item) == b"12345"
